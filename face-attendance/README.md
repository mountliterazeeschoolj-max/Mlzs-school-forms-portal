# Face Attendance (staff)

Camera-based check-in/out for staff, wired into the school portal's Supabase database.

- Staff come from `staff_records`; present/late is written to `att_employee_attendance` (same table the portal's
  Attendance module and Timetable substitutes read), using the shift times in `att_settings`.
- Raw camera sightings (check-in / latest check-out) are in `face_punches`; face templates in `face_embeddings`;
  cameras in `face_cameras`. These tables are locked from the public anon key.
- Only portal admins (`admin_emails`, role ADMIN) can open the console: Portal > Attendance > Face Attendance.

## Deploy (needs a small always-on server with Docker, ideally 4 GB RAM, on the same network as the cameras
or able to reach them)
1. `cp .env.example .env` and fill in `DOMAIN`, `DATABASE_URL` (Supabase session-pooler string + DB password),
   `SUPABASE_ANON_KEY`.
2. `docker compose up -d` (the image is built by GitHub Actions; or uncomment `build: .`).
3. Set `FACE_ATTENDANCE_URL` in the portal's `index.html` to `https://<DOMAIN>`.
4. Console > Cameras: add each camera (RTSP URL or USB index). Console > Staff: add 3 to 5 photos per person.

Database migrations are already applied to Supabase (`face_attendance_tables`, `face_mark_attendance_no_substitute_sync`).
