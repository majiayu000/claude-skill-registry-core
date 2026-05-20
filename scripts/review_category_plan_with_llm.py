#!/usr/bin/env python3
"""Review category migration candidates with an OpenAI-compatible chat model."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from category_taxonomy import CategoryTaxonomy, get_taxonomy

DEFAULT_BASE_URL = "https://token-plan-sgp.xiaomimimo.com/v1"
DEFAULT_MODEL = "mimo-v2.5-pro"
DEFAULT_API_KEY_ENV = "MIMO_API_KEY"
DEFAULT_ACTIONS = ("heuristic_reclassify", "resolve_source_conflict")
CONFIDENCE_PRIORITY = {"low": 0, "medium": 1, "high": 2}


class ChatClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        """Return assistant message content for the supplied chat messages."""


class LLMReviewError(RuntimeError):
    """Raised when the chat API request cannot produce a response."""


@dataclass(frozen=True)
class OpenAICompatibleClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout: int = 60
    max_completion_tokens: int = 512
    temperature: float = 0.0

    def complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": self.max_completion_tokens,
            "temperature": self.temperature,
            "stream": False,
        }
        request = Request(
            f"{self.base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise LLMReviewError(f"chat API returned HTTP {exc.code}: {body}") from exc
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise LLMReviewError(f"chat API request failed: {exc}") from exc

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMReviewError("chat API response did not include choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise LLMReviewError("chat API response did not include message content")
        return content.strip()


def active_category_payload(taxonomy: CategoryTaxonomy) -> list[dict[str, str]]:
    return [
        {
            "slug": definition.slug,
            "display_name": definition.display_name,
            "status": definition.status,
            "description": definition.description,
        }
        for definition in sorted(taxonomy.categories.values(), key=lambda item: item.slug)
        if definition.status != "deprecated"
    ]


def select_changes(
    plan: dict[str, Any],
    *,
    actions: set[str],
    confidences: set[str],
    limit: int | None,
    priority: str,
) -> list[dict[str, Any]]:
    changes = [
        change
        for change in plan.get("changes", [])
        if isinstance(change, dict)
        and (not actions or str(change.get("action", "")) in actions)
        and (not confidences or str(change.get("confidence", "")) in confidences)
    ]
    if priority == "risky-first":
        changes.sort(
            key=lambda change: (
                CONFIDENCE_PRIORITY.get(str(change.get("confidence", "")), 9),
                str(change.get("action", "")),
                str(change.get("path", "")),
            )
        )
    if limit is None:
        return changes
    return changes[: max(limit, 0)]


def build_messages(
    change: dict[str, Any],
    *,
    categories: list[dict[str, str]],
) -> list[dict[str, str]]:
    candidate = {
        "path": change.get("path", ""),
        "name": change.get("name", ""),
        "action": change.get("action", ""),
        "current_category": change.get("current_category", ""),
        "heuristic_proposed_category": change.get("proposed_category", ""),
        "raw_sources": change.get("raw_sources", {}),
        "resolved_sources": change.get("resolved_sources", {}),
        "signals": change.get("signals", []),
        "heuristic_reason": change.get("reason", ""),
        "score": change.get("score"),
        "current_score": change.get("current_score"),
    }
    system_prompt = (
        "You are auditing a skill registry taxonomy migration. "
        "Choose exactly one category slug from allowed_categories. "
        "Return only valid compact JSON with keys: category, confidence, reason, evidence. "
        "confidence must be a number from 0 to 1. evidence must be a short array of strings. "
        "Do not include markdown, prose outside JSON, or hidden reasoning."
    )
    user_payload = {
        "allowed_categories": categories,
        "candidate": candidate,
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def parse_json_content(content: str) -> tuple[dict[str, Any] | None, str]:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(payload, dict):
        return None, "invalid_json"
    return payload, "ok"


def normalized_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        confidence = float(value)
        if 0.0 <= confidence <= 1.0:
            return confidence
    return None


def decide_review(
    *,
    current_proposed: str,
    llm_category: str,
    llm_confidence: float | None,
    parse_status: str,
    override_confidence: float,
) -> str:
    if parse_status != "ok" or llm_confidence is None:
        return "uncertain"
    if llm_category == current_proposed:
        return "agree"
    if llm_confidence >= override_confidence:
        return "override"
    return "uncertain"


def build_review_entry(
    change: dict[str, Any],
    *,
    content: str,
    taxonomy: CategoryTaxonomy,
    override_confidence: float,
    include_raw: bool,
) -> dict[str, Any]:
    parsed, parse_status = parse_json_content(content)
    raw_category = parsed.get("category") if parsed else ""
    llm_category = taxonomy.resolve(str(raw_category), allow_unknown=True) if raw_category else ""
    allowed_category = llm_category in taxonomy.categories and (
        taxonomy.categories[llm_category].status != "deprecated"
    )
    llm_confidence = normalized_confidence(parsed.get("confidence") if parsed else None)
    if parse_status == "ok" and not allowed_category:
        parse_status = "unknown_category"
    if parse_status == "ok" and llm_confidence is None:
        parse_status = "invalid_confidence"

    decision = decide_review(
        current_proposed=str(change.get("proposed_category", "")),
        llm_category=llm_category,
        llm_confidence=llm_confidence,
        parse_status=parse_status,
        override_confidence=override_confidence,
    )
    entry = {
        "path": change.get("path", ""),
        "name": change.get("name", ""),
        "action": change.get("action", ""),
        "current_category": change.get("current_category", ""),
        "heuristic_proposed_category": change.get("proposed_category", ""),
        "llm_proposed_category": llm_category,
        "llm_confidence": llm_confidence,
        "decision": decision,
        "parse_status": parse_status,
        "review_required": True,
        "reason": parsed.get("reason", "") if parsed else "",
        "evidence": parsed.get("evidence", []) if parsed else [],
    }
    if include_raw:
        entry["raw_response"] = content
    return entry


def build_error_entry(change: dict[str, Any], error: Exception) -> dict[str, Any]:
    return {
        "path": change.get("path", ""),
        "name": change.get("name", ""),
        "action": change.get("action", ""),
        "current_category": change.get("current_category", ""),
        "heuristic_proposed_category": change.get("proposed_category", ""),
        "llm_proposed_category": "",
        "llm_confidence": None,
        "decision": "uncertain",
        "parse_status": "api_error",
        "review_required": True,
        "reason": str(error),
        "evidence": [],
    }


def build_review_report(
    plan: dict[str, Any],
    *,
    client: ChatClient,
    model: str,
    base_url: str,
    api_key_env: str,
    actions: set[str] | None = None,
    confidences: set[str] | None = None,
    limit: int | None = 25,
    priority: str = "risky-first",
    sleep_seconds: float = 0.0,
    override_confidence: float = 0.8,
    include_raw: bool = False,
    source_plan: str = "",
) -> dict[str, Any]:
    taxonomy = get_taxonomy()
    categories = active_category_payload(taxonomy)
    selected_changes = select_changes(
        plan,
        actions=actions or set(DEFAULT_ACTIONS),
        confidences=confidences or set(),
        limit=limit,
        priority=priority,
    )
    reviews: list[dict[str, Any]] = []
    for index, change in enumerate(selected_changes):
        if index and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        messages = build_messages(change, categories=categories)
        try:
            content = client.complete(messages)
            reviews.append(
                build_review_entry(
                    change,
                    content=content,
                    taxonomy=taxonomy,
                    override_confidence=override_confidence,
                    include_raw=include_raw,
                )
            )
        except LLMReviewError as exc:
            reviews.append(build_error_entry(change, exc))

    decision_counts = Counter(review["decision"] for review in reviews)
    parse_status_counts = Counter(review["parse_status"] for review in reviews)
    category_pairs = Counter(
        (review["heuristic_proposed_category"], review["llm_proposed_category"])
        for review in reviews
    )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_plan": source_plan,
        "model": model,
        "base_url": base_url,
        "policy": {
            "actions": sorted(actions or set(DEFAULT_ACTIONS)),
            "confidences": sorted(confidences or []),
            "limit": limit,
            "priority": priority,
            "sleep_seconds": sleep_seconds,
            "override_confidence": override_confidence,
            "api_key_env": api_key_env,
            "apply_mode": "review-only",
        },
        "summary": {
            "candidate_count": len(selected_changes),
            "reviewed_count": len(reviews),
            "decision_counts": dict(sorted(decision_counts.items())),
            "parse_status_counts": dict(sorted(parse_status_counts.items())),
            "category_pair_counts": [
                {
                    "heuristic_proposed_category": heuristic,
                    "llm_proposed_category": llm,
                    "count": count,
                }
                for (heuristic, llm), count in sorted(
                    category_pairs.items(),
                    key=lambda item: (-item[1], item[0][0], item[0][1]),
                )
            ],
        },
        "reviews": reviews,
        "notes": [
            "This report does not modify files.",
            f"API credentials are read from {api_key_env} and are not written to the report.",
            "LLM recommendations require human review before any archive migration is applied.",
        ],
    }


def print_text_report(report: dict[str, Any], *, limit: int) -> None:
    summary = report["summary"]
    print("LLM category review")
    print(f"Candidates: {summary['candidate_count']}")
    print(f"Reviewed: {summary['reviewed_count']}")
    print(f"Decisions: {summary['decision_counts']}")
    print(f"Parse status: {summary['parse_status_counts']}")
    for review in report["reviews"][:limit]:
        print(
            f"- {review['decision']} {review['path']}: "
            f"{review['heuristic_proposed_category']} -> "
            f"{review['llm_proposed_category']} ({review['parse_status']})"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key-env", default=DEFAULT_API_KEY_ENV)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--max-completion-tokens", type=int, default=512)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--action", action="append")
    parser.add_argument("--confidence", action="append")
    parser.add_argument("--priority", choices=["risky-first", "plan"], default="risky-first")
    parser.add_argument("--sleep-seconds", type=float, default=0.0)
    parser.add_argument("--override-confidence", type=float, default=0.8)
    parser.add_argument("--include-raw", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--print-limit", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(f"Missing API key environment variable: {args.api_key_env}")
    if not args.plan.exists():
        raise SystemExit(f"Category migration plan not found: {args.plan}")

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise SystemExit("Category migration plan must contain a JSON object")

    client = OpenAICompatibleClient(
        api_key=api_key,
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
        max_completion_tokens=args.max_completion_tokens,
        temperature=args.temperature,
    )
    report = build_review_report(
        plan,
        client=client,
        model=args.model,
        base_url=args.base_url,
        api_key_env=args.api_key_env,
        actions=set(args.action or DEFAULT_ACTIONS),
        confidences=set(args.confidence or []),
        limit=args.limit,
        priority=args.priority,
        sleep_seconds=args.sleep_seconds,
        override_confidence=args.override_confidence,
        include_raw=args.include_raw,
        source_plan=str(args.plan),
    )
    payload = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        print_text_report(report, limit=args.print_limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
