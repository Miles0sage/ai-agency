-- Agency learning table for self-improving agents
CREATE TABLE IF NOT EXISTS agency_learnings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    task_type TEXT NOT NULL,
    prompt_summary TEXT,
    model_used TEXT,
    confidence FLOAT,
    cost_usd FLOAT,
    success BOOLEAN DEFAULT true,
    output_preview TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_learnings_task_type ON agency_learnings(task_type);
CREATE INDEX IF NOT EXISTS idx_learnings_success ON agency_learnings(success) WHERE success = true;
