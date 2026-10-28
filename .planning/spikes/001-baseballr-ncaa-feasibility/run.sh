#!/usr/bin/env bash
set -euo pipefail

spike_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
image_name="roster-scout/baseballr-spike:v2.0.0"
results_dir="${spike_dir}/results"

mkdir -p "${results_dir}"

docker build --platform linux/amd64 --tag "${image_name}" "${spike_dir}"
docker image inspect "${image_name}" --format '{{.Size}}' > "${results_dir}/image-size-bytes.txt"
docker run --rm --platform linux/amd64 \
  --shm-size=1g \
  --memory=4g \
  --cpus=2 \
  --volume "${results_dir}:/spike/results" \
  --env "SPIKE_YEAR=${SPIKE_YEAR:-2026}" \
  --env "SPIKE_REPEAT=${SPIKE_REPEAT:-1}" \
  "${image_name}"
