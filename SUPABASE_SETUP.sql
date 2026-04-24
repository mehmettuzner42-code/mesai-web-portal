-- Mesai sync schema for Supabase (run in SQL Editor)
-- Safe to run multiple times.

create extension if not exists pgcrypto;

create table if not exists public.profiles (
  user_id uuid primary key references auth.users(id) on delete cascade,
  username text unique,
  daire_baskanligi text default '',
  sube_mudurlugu text default '',
  ad_soyad text default '',
  sicil_no text default '',
  ekip_kodu text default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  work_date date not null,
  start_time text not null,
  end_time text not null,
  pct60 double precision not null default 0,
  pct15 double precision not null default 0,
  pazar double precision not null default 0,
  bayram double precision not null default 0,
  description text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_entries_user_date on public.entries(user_id, work_date desc);
create unique index if not exists uq_entries_user_time on public.entries(user_id, work_date, start_time, end_time);

alter table public.profiles enable row level security;
alter table public.entries enable row level security;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='profiles' and policyname='profiles_select_own'
  ) then
    create policy profiles_select_own on public.profiles for select using (auth.uid() = user_id);
  end if;
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='profiles' and policyname='profiles_insert_own'
  ) then
    create policy profiles_insert_own on public.profiles for insert with check (auth.uid() = user_id);
  end if;
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='profiles' and policyname='profiles_update_own'
  ) then
    create policy profiles_update_own on public.profiles for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='entries' and policyname='entries_select_own'
  ) then
    create policy entries_select_own on public.entries for select using (auth.uid() = user_id);
  end if;
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='entries' and policyname='entries_insert_own'
  ) then
    create policy entries_insert_own on public.entries for insert with check (auth.uid() = user_id);
  end if;
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='entries' and policyname='entries_update_own'
  ) then
    create policy entries_update_own on public.entries for update using (auth.uid() = user_id) with check (auth.uid() = user_id);
  end if;
  if not exists (
    select 1 from pg_policies where schemaname='public' and tablename='entries' and policyname='entries_delete_own'
  ) then
    create policy entries_delete_own on public.entries for delete using (auth.uid() = user_id);
  end if;
end $$;
