#!/usr/bin/env bash
# Installs or updates Perseverer on the production server from the one configuration file.
#
#   scripts/install-production.sh [path/to/perseverer.env]     (default: ./perseverer.env)
#
# Renders quadlet/*.container with the values from perseverer.env, installs them and the env file
# into ~/.config/containers/systemd/, (re)starts the services and applies database migrations.
# Safe to re-run after editing perseverer.env or pulling a new version of the repository.
# Runs as the (non-root) user that owns the containers; see docs/DEPLOY.md.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$repo_root/perseverer.env}"
target_dir="$HOME/.config/containers/systemd"
units=(perseverer-api perseverer-worker perseverer-frontend)

if [[ ! -f "$env_file" ]]; then
    echo "error: $env_file not found -- copy perseverer.env.example to perseverer.env and fill it in." >&2
    exit 1
fi

# Parse KEY=value lines without executing the file.
declare -A cfg=(
    [PERSEVERER_IMAGE_PREFIX]="ghcr.io/jcatrouillet"
    [PERSEVERER_IMAGE_TAG]="latest"
    [PERSEVERER_HOST_DATA_DIR]="~/perseverer/data"
    [PERSEVERER_HOST_BACKUP_SSH_DIR]="~/perseverer/backup-ssh"
    [PERSEVERER_HOST_API_PORT]="8000"
    [PERSEVERER_HOST_FRONTEND_PORT]="8080"
    [PERSEVERER_SCHEDULE_TIMEZONE]="UTC"
)
while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    if [[ "$line" =~ ^[[:space:]]*([A-Z0-9_]+)[[:space:]]*=[[:space:]]*(.*)$ ]]; then
        value="${BASH_REMATCH[2]}"
        value="${value%"${value##*[![:space:]]}"}"
        cfg[${BASH_REMATCH[1]}]="$value"
    fi
done < "$env_file"

missing=()
for key in PERSEVERER_PUBLIC_BASE_URL PERSEVERER_API_KEY PERSEVERER_JWT_SECRET; do
    [[ -n "${cfg[$key]:-}" ]] || missing+=("$key")
done
if (( ${#missing[@]} )); then
    echo "error: set ${missing[*]} in $env_file" >&2
    exit 1
fi

expand_home() { local p="$1"; [[ "$p" == "~"* ]] && p="$HOME${p:1}"; printf '%s' "$p"; }
cfg[PERSEVERER_HOST_DATA_DIR]="$(expand_home "${cfg[PERSEVERER_HOST_DATA_DIR]}")"
cfg[PERSEVERER_HOST_BACKUP_SSH_DIR]="$(expand_home "${cfg[PERSEVERER_HOST_BACKUP_SSH_DIR]}")"

# The containers run as uid 1000; inside rootless Podman's user namespace that needs a chown.
for dir in "${cfg[PERSEVERER_HOST_DATA_DIR]}" "${cfg[PERSEVERER_HOST_BACKUP_SSH_DIR]}"; do
    if [[ ! -d "$dir" ]]; then
        mkdir -p "$dir"
        podman unshare chown 1000:1000 "$dir"
        echo "created $dir"
    fi
done

mkdir -p "$target_dir"
install -m 600 "$env_file" "$target_dir/perseverer.env"

render_keys=(PERSEVERER_IMAGE_PREFIX PERSEVERER_IMAGE_TAG PERSEVERER_HOST_DATA_DIR
    PERSEVERER_HOST_BACKUP_SSH_DIR PERSEVERER_HOST_API_PORT PERSEVERER_HOST_FRONTEND_PORT
    PERSEVERER_SCHEDULE_TIMEZONE)
for unit in "${units[@]}"; do
    content="$(< "$repo_root/quadlet/$unit.container")"
    for key in "${render_keys[@]}"; do
        placeholder="\${$key}"
        content="${content//"$placeholder"/${cfg[$key]}}"
    done
    printf '%s\n' "$content" > "$target_dir/$unit.container"
done
echo "installed units and perseverer.env into $target_dir"

systemctl --user daemon-reload
systemctl --user restart "${units[@]}"
systemctl --user enable --now podman-auto-update.timer >/dev/null 2>&1 || true

echo -n "waiting for the API"
for _ in $(seq 1 60); do
    if podman exec perseverer-api python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/healthz', timeout=3)" >/dev/null 2>&1; then
        echo " up"
        podman exec perseverer-api alembic upgrade head
        echo "Perseverer is running. Point your reverse proxy at port ${cfg[PERSEVERER_HOST_FRONTEND_PORT]}."
        if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q yes; then
            echo "note: run 'sudo loginctl enable-linger $USER' so the services survive logout and start at boot."
        fi
        exit 0
    fi
    echo -n "."
    sleep 2
done
echo
echo "error: the API did not become healthy; check 'journalctl --user -u perseverer-api'." >&2
exit 1
