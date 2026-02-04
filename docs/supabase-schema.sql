-- ═══════════════════════════════════════════════════════════
-- Claude Skills Registry - Supabase Schema
-- 社区版：点赞、评分、评论、浏览量
-- ═══════════════════════════════════════════════════════════

-- 1. Skills 统计表（核心）
CREATE TABLE IF NOT EXISTS skill_stats (
  skill_install TEXT PRIMARY KEY,  -- e.g., "openai/codex/skill-creator"
  likes_count INT DEFAULT 0,
  views_count INT DEFAULT 0,
  rating_sum INT DEFAULT 0,        -- 总评分
  rating_count INT DEFAULT 0,      -- 评分人数
  comments_count INT DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. 点赞记录（防重复点赞）
CREATE TABLE IF NOT EXISTS skill_likes (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  skill_install TEXT NOT NULL,
  device_id TEXT NOT NULL,         -- 设备指纹（匿名）
  user_id UUID REFERENCES auth.users,  -- 可选登录用户
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(skill_install, device_id)
);

-- 3. 评分记录
CREATE TABLE IF NOT EXISTS skill_ratings (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  skill_install TEXT NOT NULL,
  device_id TEXT NOT NULL,
  user_id UUID REFERENCES auth.users,
  rating INT CHECK (rating >= 1 AND rating <= 5),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(skill_install, device_id)
);

-- 4. 评论
CREATE TABLE IF NOT EXISTS skill_comments (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  skill_install TEXT NOT NULL,
  device_id TEXT NOT NULL,
  user_id UUID REFERENCES auth.users,
  nickname TEXT DEFAULT 'Anonymous',
  content TEXT NOT NULL CHECK (char_length(content) <= 500),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. 浏览记录（用于热度计算）
CREATE TABLE IF NOT EXISTS skill_views (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  skill_install TEXT NOT NULL,
  device_id TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════
-- 索引
-- ═══════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_skill_stats_likes ON skill_stats(likes_count DESC);
CREATE INDEX IF NOT EXISTS idx_skill_stats_views ON skill_stats(views_count DESC);
CREATE INDEX IF NOT EXISTS idx_skill_stats_rating ON skill_stats(rating_sum DESC);
CREATE INDEX IF NOT EXISTS idx_skill_likes_skill ON skill_likes(skill_install);
CREATE INDEX IF NOT EXISTS idx_skill_comments_skill ON skill_comments(skill_install);
CREATE INDEX IF NOT EXISTS idx_skill_views_skill ON skill_views(skill_install);
CREATE INDEX IF NOT EXISTS idx_skill_views_created ON skill_views(created_at DESC);

-- ═══════════════════════════════════════════════════════════
-- Row Level Security (RLS)
-- ═══════════════════════════════════════════════════════════

-- 启用 RLS
ALTER TABLE skill_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_likes ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE skill_views ENABLE ROW LEVEL SECURITY;

-- skill_stats: 所有人可读，通过函数更新
CREATE POLICY "Anyone can view skill stats"
  ON skill_stats FOR SELECT
  USING (true);

CREATE POLICY "Allow insert for stats"
  ON skill_stats FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Allow update for stats"
  ON skill_stats FOR UPDATE
  USING (true);

-- skill_likes: 所有人可读可插入
CREATE POLICY "Anyone can view likes"
  ON skill_likes FOR SELECT
  USING (true);

CREATE POLICY "Anyone can like"
  ON skill_likes FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Users can unlike their own"
  ON skill_likes FOR DELETE
  USING (true);

-- skill_ratings: 所有人可读可插入
CREATE POLICY "Anyone can view ratings"
  ON skill_ratings FOR SELECT
  USING (true);

CREATE POLICY "Anyone can rate"
  ON skill_ratings FOR INSERT
  WITH CHECK (true);

CREATE POLICY "Users can update their rating"
  ON skill_ratings FOR UPDATE
  USING (true);

-- skill_comments: 所有人可读可插入
CREATE POLICY "Anyone can view comments"
  ON skill_comments FOR SELECT
  USING (true);

CREATE POLICY "Anyone can comment"
  ON skill_comments FOR INSERT
  WITH CHECK (true);

-- skill_views: 只能插入
CREATE POLICY "Anyone can log view"
  ON skill_views FOR INSERT
  WITH CHECK (true);

-- ═══════════════════════════════════════════════════════════
-- 函数：点赞
-- ═══════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION toggle_like(p_skill_install TEXT, p_device_id TEXT)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_exists BOOLEAN;
  v_new_count INT;
BEGIN
  -- 检查是否已点赞
  SELECT EXISTS(
    SELECT 1 FROM skill_likes
    WHERE skill_install = p_skill_install AND device_id = p_device_id
  ) INTO v_exists;

  IF v_exists THEN
    -- 取消点赞
    DELETE FROM skill_likes
    WHERE skill_install = p_skill_install AND device_id = p_device_id;

    UPDATE skill_stats
    SET likes_count = GREATEST(0, likes_count - 1), updated_at = NOW()
    WHERE skill_install = p_skill_install;
  ELSE
    -- 添加点赞
    INSERT INTO skill_likes (skill_install, device_id)
    VALUES (p_skill_install, p_device_id);

    -- 更新或创建统计
    INSERT INTO skill_stats (skill_install, likes_count)
    VALUES (p_skill_install, 1)
    ON CONFLICT (skill_install)
    DO UPDATE SET likes_count = skill_stats.likes_count + 1, updated_at = NOW();
  END IF;

  -- 返回新的点赞数
  SELECT likes_count INTO v_new_count
  FROM skill_stats WHERE skill_install = p_skill_install;

  RETURN json_build_object(
    'liked', NOT v_exists,
    'count', COALESCE(v_new_count, 0)
  );
END;
$$;

-- ═══════════════════════════════════════════════════════════
-- 函数：评分
-- ═══════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION submit_rating(p_skill_install TEXT, p_device_id TEXT, p_rating INT)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_old_rating INT;
  v_avg NUMERIC;
  v_count INT;
BEGIN
  -- 检查是否已评分
  SELECT rating INTO v_old_rating
  FROM skill_ratings
  WHERE skill_install = p_skill_install AND device_id = p_device_id;

  IF v_old_rating IS NOT NULL THEN
    -- 更新评分
    UPDATE skill_ratings
    SET rating = p_rating
    WHERE skill_install = p_skill_install AND device_id = p_device_id;

    UPDATE skill_stats
    SET rating_sum = rating_sum - v_old_rating + p_rating, updated_at = NOW()
    WHERE skill_install = p_skill_install;
  ELSE
    -- 新评分
    INSERT INTO skill_ratings (skill_install, device_id, rating)
    VALUES (p_skill_install, p_device_id, p_rating);

    INSERT INTO skill_stats (skill_install, rating_sum, rating_count)
    VALUES (p_skill_install, p_rating, 1)
    ON CONFLICT (skill_install)
    DO UPDATE SET
      rating_sum = skill_stats.rating_sum + p_rating,
      rating_count = skill_stats.rating_count + 1,
      updated_at = NOW();
  END IF;

  -- 返回新的平均分
  SELECT rating_sum::NUMERIC / NULLIF(rating_count, 0), rating_count
  INTO v_avg, v_count
  FROM skill_stats WHERE skill_install = p_skill_install;

  RETURN json_build_object(
    'average', ROUND(COALESCE(v_avg, 0), 1),
    'count', COALESCE(v_count, 0),
    'userRating', p_rating
  );
END;
$$;

-- ═══════════════════════════════════════════════════════════
-- 函数：记录浏览
-- ═══════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION log_view(p_skill_install TEXT, p_device_id TEXT)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_count INT;
BEGIN
  -- 插入浏览记录
  INSERT INTO skill_views (skill_install, device_id)
  VALUES (p_skill_install, p_device_id);

  -- 更新统计
  INSERT INTO skill_stats (skill_install, views_count)
  VALUES (p_skill_install, 1)
  ON CONFLICT (skill_install)
  DO UPDATE SET views_count = skill_stats.views_count + 1, updated_at = NOW();

  SELECT views_count INTO v_count
  FROM skill_stats WHERE skill_install = p_skill_install;

  RETURN COALESCE(v_count, 1);
END;
$$;

-- ═══════════════════════════════════════════════════════════
-- 函数：添加评论
-- ═══════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION add_comment(
  p_skill_install TEXT,
  p_device_id TEXT,
  p_content TEXT,
  p_nickname TEXT DEFAULT 'Anonymous'
)
RETURNS JSON
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_comment skill_comments;
BEGIN
  -- 插入评论
  INSERT INTO skill_comments (skill_install, device_id, nickname, content)
  VALUES (p_skill_install, p_device_id, p_nickname, p_content)
  RETURNING * INTO v_comment;

  -- 更新统计
  INSERT INTO skill_stats (skill_install, comments_count)
  VALUES (p_skill_install, 1)
  ON CONFLICT (skill_install)
  DO UPDATE SET comments_count = skill_stats.comments_count + 1, updated_at = NOW();

  RETURN json_build_object(
    'id', v_comment.id,
    'nickname', v_comment.nickname,
    'content', v_comment.content,
    'created_at', v_comment.created_at
  );
END;
$$;

-- ═══════════════════════════════════════════════════════════
-- 视图：热门 Skills（基于真实数据）
-- ═══════════════════════════════════════════════════════════
CREATE OR REPLACE VIEW popular_skills AS
SELECT
  skill_install,
  likes_count,
  views_count,
  CASE WHEN rating_count > 0
    THEN ROUND(rating_sum::NUMERIC / rating_count, 1)
    ELSE 0
  END as avg_rating,
  rating_count,
  comments_count,
  -- 热度分数 = 点赞*3 + 评分*2 + 评论*2 + 浏览*0.1
  (likes_count * 3 + rating_count * 2 + comments_count * 2 + views_count * 0.1) as hot_score,
  updated_at
FROM skill_stats
ORDER BY hot_score DESC;
