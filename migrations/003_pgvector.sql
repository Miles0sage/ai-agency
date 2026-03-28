-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Episodic memory with embeddings for similarity search
CREATE TABLE IF NOT EXISTS agency_episodes (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    task_type TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt_summary TEXT,
    output_summary TEXT,
    model_used TEXT,
    confidence FLOAT,
    cost_usd FLOAT,
    success BOOLEAN DEFAULT true,
    embedding vector(384),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_episodes_task_type ON agency_episodes(task_type);
CREATE INDEX IF NOT EXISTS idx_episodes_embedding ON agency_episodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);
