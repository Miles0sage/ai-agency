-- AI Agency Tables
create table if not exists agency_clients (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  email text,
  api_key text unique default encode(gen_random_bytes(32), 'hex'),
  credits_usd numeric default 0,
  created_at timestamptz default now()
);

create table if not exists agency_tasks (
  id uuid primary key default gen_random_uuid(),
  title text not null,
  description text,
  task_type text check (task_type in ('coding','research','content','design','review')),
  status text default 'pending' check (status in ('pending','in_progress','review','completed','failed')),
  client_id uuid references agency_clients(id),
  priority int default 5,
  result jsonb,
  cost_usd numeric default 0,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  completed_at timestamptz
);

create table if not exists agency_subtasks (
  id uuid primary key default gen_random_uuid(),
  parent_task_id uuid references agency_tasks(id) on delete cascade,
  stage text check (stage in ('requirements','plan','execute','verify','deliver')),
  status text default 'pending',
  worker text,
  output text,
  cost_usd numeric default 0,
  duration_secs numeric,
  created_at timestamptz default now(),
  completed_at timestamptz
);

-- Indexes
create index if not exists idx_tasks_status on agency_tasks(status);
create index if not exists idx_tasks_client on agency_tasks(client_id);
create index if not exists idx_subtasks_parent on agency_subtasks(parent_task_id);
create index if not exists idx_subtasks_status on agency_subtasks(status);

-- Auto-update trigger
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trg_tasks_updated
  before update on agency_tasks
  for each row execute function update_updated_at();

-- RLS
alter table agency_clients enable row level security;
alter table agency_tasks enable row level security;
alter table agency_subtasks enable row level security;

create policy "service_all" on agency_clients for all using (true);
create policy "service_all" on agency_tasks for all using (true);
create policy "service_all" on agency_subtasks for all using (true);
