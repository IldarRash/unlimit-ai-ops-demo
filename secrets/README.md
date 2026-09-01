# Local secret files

Copy each `*.example` file to the same name without `.example`, replace its
contents with a random value of at least 20 characters, and keep the resulting
files local. Docker Compose mounts them into Alertmanager and the incident API;
they are excluded from Git.
