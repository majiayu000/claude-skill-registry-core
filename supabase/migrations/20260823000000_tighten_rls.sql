-- Tighten community RLS: stop anonymous direct writes to ranking stats,
-- likes, and comments. Writes go through SECURITY DEFINER RPCs.

DROP POLICY IF EXISTS "Anyone can insert stats" ON skill_stats;
DROP POLICY IF EXISTS "Anyone can update stats" ON skill_stats;
DROP POLICY IF EXISTS "Users can delete own likes" ON skill_likes;
DROP POLICY IF EXISTS "Users can update own comments" ON skill_comments;

CREATE OR REPLACE FUNCTION toggle_like(p_skill_install TEXT, p_device_id TEXT)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_exists BOOLEAN;
  v_new_count INT;
BEGIN
  SELECT EXISTS(
    SELECT 1 FROM skill_likes
    WHERE skill_install = p_skill_install AND device_id = p_device_id
  ) INTO v_exists;

  IF v_exists THEN
    DELETE FROM skill_likes
    WHERE skill_install = p_skill_install AND device_id = p_device_id;

    UPDATE skill_stats
    SET likes_count = GREATEST(0, likes_count - 1), updated_at = NOW()
    WHERE skill_install = p_skill_install;
  ELSE
    INSERT INTO skill_likes (skill_install, device_id)
    VALUES (p_skill_install, p_device_id);

    INSERT INTO skill_stats (skill_install, likes_count)
    VALUES (p_skill_install, 1)
    ON CONFLICT (skill_install)
    DO UPDATE SET likes_count = skill_stats.likes_count + 1, updated_at = NOW();
  END IF;

  SELECT likes_count INTO v_new_count
  FROM skill_stats WHERE skill_install = p_skill_install;

  RETURN json_build_object(
    'liked', NOT v_exists,
    'count', COALESCE(v_new_count, 0)
  );
END;
$$;

CREATE OR REPLACE FUNCTION add_comment(
  p_skill_install TEXT,
  p_device_id TEXT,
  p_content TEXT,
  p_nickname TEXT DEFAULT 'Anonymous',
  p_rating INT DEFAULT NULL
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_comment_id UUID;
BEGIN
  INSERT INTO skill_comments (skill_install, device_id, content, nickname, rating)
  VALUES (p_skill_install, p_device_id, p_content, p_nickname, p_rating)
  RETURNING id INTO v_comment_id;

  INSERT INTO skill_stats (skill_install, comments_count)
  VALUES (p_skill_install, 1)
  ON CONFLICT (skill_install)
  DO UPDATE SET comments_count = skill_stats.comments_count + 1, updated_at = NOW();

  RETURN json_build_object('id', v_comment_id, 'success', true);
END;
$$;

CREATE OR REPLACE FUNCTION get_skill_stats(p_skill_install TEXT, p_device_id TEXT)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_stats skill_stats%ROWTYPE;
  v_liked BOOLEAN;
  v_favorited BOOLEAN;
BEGIN
  SELECT * INTO v_stats FROM skill_stats WHERE skill_install = p_skill_install;

  SELECT EXISTS(
    SELECT 1 FROM skill_likes
    WHERE skill_install = p_skill_install AND device_id = p_device_id
  ) INTO v_liked;

  SELECT EXISTS(
    SELECT 1 FROM user_favorites
    WHERE skill_install = p_skill_install AND device_id = p_device_id
  ) INTO v_favorited;

  RETURN json_build_object(
    'likes_count', COALESCE(v_stats.likes_count, 0),
    'comments_count', COALESCE(v_stats.comments_count, 0),
    'views_count', COALESCE(v_stats.views_count, 0),
    'liked', COALESCE(v_liked, false),
    'favorited', COALESCE(v_favorited, false)
  );
END;
$$;

CREATE OR REPLACE FUNCTION get_trending_skills(p_limit INT DEFAULT 50)
RETURNS TABLE (
  skill_install TEXT,
  likes_count INT,
  comments_count INT,
  score NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
  RETURN QUERY
  SELECT
    s.skill_install,
    s.likes_count,
    s.comments_count,
    (s.likes_count * 2 + s.comments_count)::NUMERIC AS score
  FROM skill_stats s
  ORDER BY score DESC, s.updated_at DESC
  LIMIT p_limit;
END;
$$;
