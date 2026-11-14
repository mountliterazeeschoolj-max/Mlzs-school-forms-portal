-- Already applied to the Supabase project (migrations face_attendance_tables + face_mark_attendance_no_substitute_sync).
-- Kept here for reference / re-creating the database.
create table if not exists public.face_cameras (
  id bigint generated always as identity primary key,
  name text not null,
  source text not null,
  zone text not null default 'BOTH' check (zone in ('CHECK_IN','CHECK_OUT','BOTH')),
  enabled boolean not null default true,
  created_at timestamptz not null default now()
);
create table if not exists public.face_embeddings (
  id bigint generated always as identity primary key,
  staff_id uuid not null references public.staff_records(id) on delete cascade,
  vector bytea not null,
  created_at timestamptz not null default now()
);
create index if not exists face_embeddings_staff_idx on public.face_embeddings(staff_id);
create table if not exists public.face_punches (
  id bigint generated always as identity primary key,
  staff_id uuid not null references public.staff_records(id) on delete cascade,
  punched_at timestamptz not null default now(),
  kind text not null check (kind in ('IN','OUT')),
  camera_id bigint references public.face_cameras(id) on delete set null,
  score real
);
create index if not exists face_punches_staff_time_idx on public.face_punches(staff_id, punched_at desc);
alter table public.face_cameras enable row level security;
alter table public.face_embeddings enable row level security;
alter table public.face_punches enable row level security;
revoke all on public.face_cameras, public.face_embeddings, public.face_punches from anon, authenticated;
grant select on public.face_punches to authenticated;
drop policy if exists "admin can select face_punches" on public.face_punches;
create policy "admin can select face_punches" on public.face_punches for select to authenticated using (public.is_admin());

create or replace function public.face_mark_attendance(p_staff uuid, p_at timestamptz)
returns text language plpgsql security definer set search_path to 'public', 'pg_temp' as $$
declare
  v_local timestamp := p_at at time zone 'Asia/Kolkata';
  v_date date := v_local::date;
  v_time time := v_local::time;
  s record; v_status text; v_id bigint;
begin
  if exists (select 1 from public.att_employee_attendance where teacher_id = p_staff and attendance_date = v_date) then
    return null;
  end if;
  select * into s from public.att_settings where id = 1;
  v_status := case when v_time > s.shift_start + make_interval(mins => s.late_grace_minutes) then 'LATE' else 'PRESENT' end;
  insert into public.att_employee_attendance (teacher_id, attendance_date, status, remarks, marked_by)
  values (p_staff, v_date, v_status, 'Face recognition check-in ' || to_char(v_time, 'HH24:MI'), 'face-attendance')
  returning id into v_id;
  insert into public.att_employee_attendance_log (attendance_id, teacher_id, attendance_date, old_status, new_status, reason, changed_by)
  values (v_id, p_staff, v_date, null, v_status, 'Face recognition check-in', 'face-attendance');
  return v_status;
end; $$;
revoke all on function public.face_mark_attendance(uuid, timestamptz) from public, anon, authenticated;
