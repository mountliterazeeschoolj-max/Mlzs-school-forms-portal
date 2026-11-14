# Face Attendance (staff)

Camera-based check-in/out for staff, wired into the school portal's Supabase database.

- Staff come from `staff_records`; present/late is written to `att_employee_attendance` (same table the portal's
  Attendance module and Timetable substitutes read), using the shift times in `att_settings`.
- Raw camera sightings (check-in / latest check-out) are in `face_punches`; face templates in `face_embeddings`;
  cameras in `face_cameras`. These tables are locked from the public anon key.
- Only portal admins (`admin_emails`, role ADMIN) can open the console: Portal > Attendance > Face Attendance.

## Deploy (always-on school computer + Tailscale Funnel for free HTTPS)
1. `cp .env.example .env` and fill in `DATABASE_URL` (Supabase session-pooler string + DB password, URL-encode
   special characters) and `SUPABASE_ANON_KEY`.
2. `docker compose up -d --build`
3. `sudo tailscale funnel --bg 8000` and note the https address it prints.
4. Put that address in `face-config.js` in the portal repo root.
5. Portal > Attendance > Face Attendance. Add cameras and 3 to 5 photos per staff member.

Database migrations are already applied to Supabase (see `supabase/`).
