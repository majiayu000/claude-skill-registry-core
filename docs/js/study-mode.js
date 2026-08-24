/**
 * Opt-in, local-only usability study recorder.
 *
 * The recorder is available only when the page is opened with ?study=1. It
 * stores coarse interaction events in sessionStorage after the participant
 * explicitly starts the study. It never transmits study events and never
 * stores search text, referrers, user-agent strings, or persistent device IDs.
 */
(function initRegistryStudy(globalScope) {
    'use strict';

    const STORAGE_KEY = 'registry-usability-study-v1';
    const SCHEMA_VERSION = 1;
    const ALLOWED_EVENTS = Object.freeze({
        search_submitted: ['query_length_bucket', 'result_count_bucket', 'source'],
        skill_detail_opened: ['skill_install', 'source_view'],
        favorite_toggled: ['skill_install', 'selected'],
        install_copy_clicked: ['skill_install'],
        github_opened: ['skill_install'],
        full_search_requested: []
    });

    function sanitizeToken(value, maxLength = 160) {
        return String(value ?? '')
            .replace(/[^a-zA-Z0-9_./:@+-]/g, '')
            .slice(0, maxLength);
    }

    function bucketQueryLength(query) {
        const length = String(query ?? '').trim().length;
        if (length === 0) return '0';
        if (length <= 3) return '1-3';
        if (length <= 10) return '4-10';
        return '11+';
    }

    function bucketResultCount(count) {
        const numericCount = Number(count);
        if (!Number.isFinite(numericCount) || numericCount <= 0) return '0';
        if (numericCount <= 10) return '1-10';
        if (numericCount <= 100) return '11-100';
        return '101+';
    }

    function sanitizeDetails(eventName, details = {}) {
        const allowedKeys = ALLOWED_EVENTS[eventName];
        if (!allowedKeys) return null;

        const sanitized = {};
        allowedKeys.forEach(key => {
            if (!(key in details)) return;
            if (key === 'selected') {
                sanitized[key] = Boolean(details[key]);
                return;
            }
            sanitized[key] = sanitizeToken(details[key]);
        });
        return sanitized;
    }

    function createMemoryStorage() {
        let value = null;
        return {
            getItem() { return value; },
            setItem(_key, nextValue) { value = nextValue; },
            removeItem() { value = null; }
        };
    }

    function parseStoredSession(storage) {
        try {
            const raw = storage.getItem(STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (parsed?.schema_version !== SCHEMA_VERSION || !Array.isArray(parsed.events)) {
                return null;
            }
            return parsed;
        } catch (_error) {
            return null;
        }
    }

    function clone(value) {
        return JSON.parse(JSON.stringify(value));
    }

    function createRecorder(options = {}) {
        const storage = options.storage || createMemoryStorage();
        const now = options.now || (() => Date.now());
        const isoNow = options.isoNow || (() => new Date(now()).toISOString());
        const makeId = options.makeId || (() => {
            if (globalScope.crypto?.randomUUID) return globalScope.crypto.randomUUID();
            return `study-${now()}-${Math.random().toString(16).slice(2)}`;
        });
        let session = parseStoredSession(storage);

        function persist() {
            storage.setItem(STORAGE_KEY, JSON.stringify(session));
        }

        function start(cohort = '') {
            session = {
                schema_version: SCHEMA_VERSION,
                session_id: sanitizeToken(makeId(), 80),
                cohort: sanitizeToken(cohort, 40),
                started_at: isoNow(),
                started_ms: now(),
                finished_at: null,
                active: true,
                events: []
            };
            persist();
            return clone(session);
        }

        function track(eventName, details = {}) {
            if (!session?.active) return false;
            const sanitizedDetails = sanitizeDetails(eventName, details);
            if (sanitizedDetails === null) return false;

            session.events.push({
                name: eventName,
                elapsed_ms: Math.max(0, now() - session.started_ms),
                details: sanitizedDetails
            });
            persist();
            return true;
        }

        function finish() {
            if (!session) return null;
            session.active = false;
            session.finished_at = isoNow();
            persist();
            return summary();
        }

        function summary() {
            if (!session) return null;
            const result = clone(session);
            delete result.started_ms;
            return result;
        }

        function clear() {
            session = null;
            storage.removeItem(STORAGE_KEY);
        }

        return {
            start,
            track,
            finish,
            summary,
            clear,
            isActive: () => Boolean(session?.active)
        };
    }

    function initializeBrowserStudy() {
        if (!globalScope.document || !globalScope.location) return null;
        const params = new URLSearchParams(globalScope.location.search);
        if (params.get('study') !== '1') return null;

        const panel = globalScope.document.getElementById('study-panel');
        const startButton = globalScope.document.getElementById('study-start');
        const finishButton = globalScope.document.getElementById('study-finish');
        const downloadButton = globalScope.document.getElementById('study-download');
        const status = globalScope.document.getElementById('study-status');
        if (!panel || !startButton || !finishButton || !downloadButton || !status) {
            throw new Error('Study mode controls are missing from the page');
        }

        const recorder = createRecorder({ storage: globalScope.sessionStorage });
        const cohort = params.get('cohort') || '';
        let lastSkillInstall = '';
        panel.classList.remove('hidden');

        function visibleResultCount() {
            const text = globalScope.document.getElementById('result-count')?.textContent || '';
            const match = text.replace(/,/g, '').match(/\d+/);
            return match ? Number(match[0]) : 0;
        }

        function currentSourceView() {
            const searchResults = globalScope.document.getElementById('search-results');
            if (searchResults && !searchResults.classList.contains('hidden')) return 'search';
            return globalScope.document.querySelector('.nav-tab.active')?.dataset.view || 'unknown';
        }

        function installFromTarget(target) {
            return target.closest('[data-install]')?.dataset.install || lastSkillInstall;
        }

        globalScope.document.addEventListener('keydown', event => {
            if (event.key !== 'Enter' || event.target.id !== 'search-input') return;
            globalScope.setTimeout(() => {
                recorder.track('search_submitted', {
                    query_length_bucket: bucketQueryLength(event.target.value),
                    result_count_bucket: bucketResultCount(visibleResultCount()),
                    source: 'enter'
                });
            }, 0);
        }, true);

        globalScope.document.addEventListener('click', event => {
            const target = event.target;
            const quickTag = target.closest('#quick-tags .tag');
            if (quickTag) {
                globalScope.setTimeout(() => {
                    recorder.track('search_submitted', {
                        query_length_bucket: bucketQueryLength(quickTag.dataset.query),
                        result_count_bucket: bucketResultCount(visibleResultCount()),
                        source: 'quick_tag'
                    });
                }, 0);
                return;
            }

            if (target.closest('#search-all-btn')) {
                recorder.track('full_search_requested');
                return;
            }

            const favoriteButton = target.closest('.favorite-btn');
            if (favoriteButton) {
                const install = installFromTarget(favoriteButton);
                globalScope.setTimeout(() => {
                    recorder.track('favorite_toggled', {
                        skill_install: install,
                        selected: favoriteButton.classList.contains('active')
                    });
                }, 0);
                return;
            }

            const copyButton = target.closest('.copy-btn');
            if (copyButton) {
                recorder.track('install_copy_clicked', {
                    skill_install: installFromTarget(copyButton)
                });
                return;
            }

            const githubLink = target.closest('#modal-body a[href*="github.com/"]');
            if (githubLink) {
                recorder.track('github_opened', { skill_install: lastSkillInstall });
                return;
            }

            const skillCard = target.closest('.skill-card, .leaderboard-card, .similar-card');
            if (skillCard?.dataset.install) {
                lastSkillInstall = skillCard.dataset.install;
                recorder.track('skill_detail_opened', {
                    skill_install: lastSkillInstall,
                    source_view: currentSourceView()
                });
            }
        }, true);

        function refreshControls(message) {
            const active = recorder.isActive();
            startButton.disabled = active;
            finishButton.disabled = !active;
            downloadButton.disabled = !recorder.summary();
            status.textContent = message || (active
                ? 'Recording coarse interactions in this tab.'
                : 'Not recording. Start only after the participant agrees.');
        }

        function serializeSummary() {
            const summary = recorder.summary();
            if (!summary) throw new Error('No study session is available');
            return JSON.stringify(summary, null, 2);
        }

        startButton.addEventListener('click', () => {
            recorder.start(cohort);
            refreshControls('Recording started. No search text or personal identifier is stored.');
        });

        finishButton.addEventListener('click', async () => {
            recorder.finish();
            try {
                await globalScope.navigator.clipboard.writeText(serializeSummary());
                refreshControls('Study finished. Summary copied to the clipboard.');
            } catch (error) {
                console.error('Unable to copy study summary:', error);
                refreshControls('Study finished, but copying failed. Use Download JSON instead.');
            }
        });

        downloadButton.addEventListener('click', () => {
            try {
                const blob = new Blob([serializeSummary()], { type: 'application/json' });
                const url = URL.createObjectURL(blob);
                const link = globalScope.document.createElement('a');
                link.href = url;
                link.download = `registry-study-${recorder.summary().session_id}.json`;
                link.click();
                URL.revokeObjectURL(url);
                refreshControls('Study summary downloaded.');
            } catch (error) {
                console.error('Unable to download study summary:', error);
                refreshControls('Download failed. Finish the study and copy the summary instead.');
            }
        });

        refreshControls();
        return recorder;
    }

    const api = {
        ALLOWED_EVENTS,
        bucketQueryLength,
        bucketResultCount,
        sanitizeDetails,
        createRecorder,
        recorder: null,
        track(eventName, details) {
            return api.recorder?.track(eventName, details) || false;
        },
        isActive() {
            return Boolean(api.recorder?.isActive());
        }
    };

    globalScope.RegistryStudy = api;
    api.recorder = initializeBrowserStudy();

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = api;
    }
})(typeof window !== 'undefined' ? window : globalThis);
