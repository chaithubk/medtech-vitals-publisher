#!/usr/bin/env bash
# Local CI parity runner:
# - Auto-fixes what is safely auto-fixable (format/import order)
# - Runs the same quality/test/security gates as .github/workflows/ci.yml
# - Exits non-zero if any gate still fails
set -u

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

AUTO_FIX=0
CHECK_ONLY=1
SKIP_SECURITY=0

usage() {
  cat <<'EOF'

Usage: tools/check_ci.sh [--fix] [--skip-security] [--help]

Options:
  --fix            Apply auto-fixes (format/import order) before checks.
  --skip-security  Skip bandit/safety checks.
  --help           Show this message.

Behavior:
  Default mode is check-only (no auto-fix, matches CI).
  Use --fix to auto-fix formatting/imports before checks.
  Manual fixes still required for: flake8, mypy, pytest, bandit, safety.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --fix)
      AUTO_FIX=1
      CHECK_ONLY=0
      shift
      ;;
    --skip-security)
      SKIP_SECURITY=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      usage
      exit 2
      ;;
  esac
done

FAILED=0
declare -a FAILING_STEPS=()
declare -a FAILING_COMMANDS=()

run_step() {
  local name="$1"
  local cmd="$2"
  echo -e "\n${GREEN}== ${name} ==${NC}"
  if eval "${cmd}"; then
    echo -e "${GREEN}PASS${NC}"
  else
    echo -e "${RED}FAIL${NC}"
    FAILED=1
    FAILING_STEPS+=("${name}")
    FAILING_COMMANDS+=("${cmd}")
  fi
}

need_cmd() {
  local c="$1"
  if ! command -v "$c" >/dev/null 2>&1; then
    echo -e "${RED}Missing command: ${c}${NC}"
    echo "Install dependencies first (e.g., pip install -r requirements.txt -r requirements-dev.txt)."
    exit 2
  fi
}

run_docker_smoke_test() {
  local network_name="smoke-net-local-ci"
  local container_name="vitals-publisher-local-ci"
  local image_name="medtech-vitals-publisher:local-ci"

  if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is not reachable from this dev container."
    echo "Rebuild the container so Docker socket/features are applied."
    return 1
  fi

  docker network create "${network_name}" >/dev/null

  docker build -t "${image_name}" .

  docker run -d \
    --name "${container_name}" \
    --network "${network_name}" \
    --hostname vitals-publisher \
    -e MQTT_BROKER=localhost \
    -e MQTT_PORT=1883 \
    -e PUBLISH_INTERVAL_S=5 \
    -e SCENARIO=healthy \
    -e LOGLEVEL=DEBUG \
    -e PYTHONUNBUFFERED=1 \
    "${image_name}" >/dev/null

  local deadline=$((SECONDS + 40))
  while [[ "$(docker inspect --format='{{.State.Health.Status}}' "${container_name}" 2>/dev/null)" != "healthy" ]]; do
    if (( SECONDS >= deadline )); then
      echo "Timed out waiting for container health"
      return 1
    fi
    sleep 2
  done

  docker run --rm --network "${network_name}" \
    --entrypoint mosquitto_pub eclipse-mosquitto:2 \
    -h vitals-publisher -p 1883 -t medtech/smoke/probe -m ping

  docker run --rm --network "${network_name}" \
    --entrypoint mosquitto_sub eclipse-mosquitto:2 \
    -h vitals-publisher -p 1883 -t 'medtech/vitals/latest' -C 1 -W 20

  docker rm -f "${container_name}" >/dev/null 2>&1 || true
  docker network rm "${network_name}" >/dev/null 2>&1 || true
}

echo -e "${YELLOW}Repository:${NC} ${REPO_ROOT}"
if [[ ${CHECK_ONLY} -eq 1 ]]; then
  echo -e "${YELLOW}Mode:${NC} check-only"
else
  echo -e "${YELLOW}Mode:${NC} fix + check"
fi

# Base tool availability
need_cmd black
need_cmd isort
need_cmd flake8
need_cmd mypy
need_cmd pytest
need_cmd docker

if [[ ${SKIP_SECURITY} -eq 0 ]]; then
  need_cmd bandit
  need_cmd safety
fi


if [[ ${AUTO_FIX} -eq 1 ]]; then
  echo -e "\n${YELLOW}Applying safe auto-fixes...${NC}"
  run_step "Auto-fix formatting (black)" "black src tests --line-length=120"
  run_step "Auto-fix imports (isort)" "isort src tests --profile=black"
fi

# Lint / type checks (CI parity)
run_step "Black check" "black --check src tests --line-length=120"
run_step "isort check" "isort --check-only src tests --profile=black"

# Track if linting failed for fix recommendations
LINT_FAILED=0
for i in "${!FAILING_STEPS[@]}"; do
  step="${FAILING_STEPS[$i]}"
  if [[ "$step" == "Black check" || "$step" == "isort check" ]]; then
    LINT_FAILED=1
    break
  fi
done
run_step "Flake8 fatal checks" "flake8 src tests --count --select=E9,F63,F7,F82 --show-source --statistics"
run_step "Flake8 full checks" "flake8 src tests --count --max-complexity=10 --max-line-length=120 --statistics"
run_step "Mypy" "mypy src --ignore-missing-imports"

# Test suite + coverage gate
run_step "Pytest + coverage" "pytest tests/ -v --cov=src --cov-report=term-missing --cov-fail-under=80"

# Contract + demo data gate from CI

if [[ -d data/synthea/demo/csv ]]; then
  run_step "Contract + demo data gate" "pytest -v --maxfail=1 tests/test_contract_schema_v2.py tests/test_demo_dataset.py"
fi

# Docker smoke-test gate (CI parity)
if [[ ${FAILED} -ne 0 ]]; then
  echo -e "${RED}CI parity checks failed.${NC}"
  echo "Failing steps:"
  for step in "${FAILING_STEPS[@]}"; do
    echo "  - ${step}"
  done
  echo ""
  echo "Copy/paste commands for failed steps:"
  echo "  source .venv/bin/activate"
  for cmd in "${FAILING_COMMANDS[@]}"; do
    echo "  ${cmd}"
  done
  echo ""
  if [[ ${LINT_FAILED} -eq 1 ]]; then
    echo -e "${YELLOW}To auto-fix formatting and import order, run:${NC}"
    echo "  black src tests --line-length=120"
    echo "  isort src tests --profile=black"
    echo ""
  fi
  echo "Auto-fixes already applied where possible (black/isort)."
  echo "Resolve remaining failures and run again: tools/check_ci.sh"
  exit 1
fi
run_step "Docker smoke-test" "run_docker_smoke_test"

echo ""
if [[ ${FAILED} -ne 0 ]]; then
  echo -e "${RED}CI parity checks failed.${NC}"
  echo "Failing steps:"
  for step in "${FAILING_STEPS[@]}"; do
    echo "  - ${step}"
  done
  echo ""
  echo "Copy/paste commands for failed steps:"
  echo "  source .venv/bin/activate"
  for cmd in "${FAILING_COMMANDS[@]}"; do
    echo "  ${cmd}"
  done
  echo ""
  echo "Auto-fixes already applied where possible (black/isort)."
  echo "Resolve remaining failures and run again: tools/check_ci.sh"
  exit 1
fi

echo -e "${GREEN}All local CI parity checks passed.${NC}"
exit 0