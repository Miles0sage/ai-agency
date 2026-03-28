-- Production upgrades: reviewer loop, budget caps, decomposition, observability

-- Add columns to shared tasks table (used by agency worker)
alter table tasks add column if not exists parent_task_id uuid references tasks(id) on delete cascade;
alter table tasks add column if not exists trace_id uuid default gen_random_uuid();
alter table tasks add column if not exists budget_cap_usd numeric default 0.10;
alter table tasks add column if not exists retry_count int default 0;
alter table tasks add column if not exists scoped_context jsonb;
alter table tasks add column if not exists department text;
alter table tasks add column if not exists model_used text;

-- agency_subtasks table (create if missing from migration 001)
create table if not exists agency_subtasks (
  id uuid primary key default gen_random_uuid(),
  parent_task_id uuid references tasks(id) on delete cascade,
  stage text,
  status text default 'pending',
  worker text,
  output text,
  cost_usd numeric default 0,
  duration_secs numeric,
  retry_count int default 0,
  review_score text,
  created_at timestamptz default now(),
  completed_at timestamptz
);

-- Indexes
create index if not exists idx_tasks_parent on tasks(parent_task_id);
create index if not exists idx_tasks_trace on tasks(trace_id);
create index if not exists idx_tasks_dept on tasks(department);
create index if not exists idx_subtasks_parent on agency_subtasks(parent_task_id);
create index if not exists idx_subtasks_stage on agency_subtasks(stage, status);

-- RLS for new table (service role bypasses, but enable for safety)
alter table agency_subtasks enable row level security;
create policy if not exists "service_all_subtasks" on agency_subtasks for all using (true);

-- Thompson bandit state table (agency worker)
create table if not exists agency_bandit_state (
  id uuid primary key default gen_random_uuid(),
  model text not null,
  task_type text not null,
  successes int default 0,
  failures int default 0,
  updated_at timestamptz default now(),
  unique(model, task_type)
);
create index if not exists idx_bandit_model_type on agency_bandit_state(model, task_type);
alter table agency_bandit_state enable row level security;
create policy if not exists "service_all_bandit" on agency_bandit_state for all using (true);

-- Add review_score column to agency_subtasks (written by quality gate)
alter table agency_subtasks add column if not exists review_score text;
