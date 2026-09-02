# Local secret files

Copy each `*.example` file to the same name without `.example`, replace its
contents with a random value of at least 20 characters, and keep the resulting
files local. Docker Compose mounts them into Alertmanager and the incident API;
they are excluded from Git.

`postgres_password` is shared only by the local PostgreSQL container and the
Incident API entrypoint. The entrypoint builds `DATABASE_URL` in memory and
never prints the password. Railway supplies its managed `DATABASE_URL`
directly, so this file is not used there.
