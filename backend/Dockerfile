# syntax=docker/dockerfile:1
#
# Single Lambda container for Dossier.
#
# One image, one handler (lambda_handler.handler). Dispatch on event shape:
#   - {"investigation_id": "<uuid>"}  -> runner.handler (LangGraph investigation)
#   - HTTP event (Function URL payload)  -> FastAPI via Mangum
#
# Base: public.ecr.aws/lambda/python:3.12 (Amazon Linux 2023, Python 3.12
# pre-wired, LAMBDA_TASK_ROOT=/var/task, LAMBDA_RUNTIME_DIR=/var/runtime).
#
# Why poppler: MarkItDown / pdf2image (Phase 5 PDF upload path) shells out to
# `pdftoppm` at OS level. Even if the PDF surface isn't live on day-1 of the
# demo, installing it now keeps a single image definition across phases.
#
# Why --platform linux/amd64 at build time (Makefile sets it): M-series Macs
# default to arm64; Lambda's base image tag here is x86_64. Cross-building now
# is cheaper than debugging an "exec format error" on cold-start later.
#
# Why uv for install: reads pyproject.toml + uv.lock directly, so the image
# install matches local dev byte-for-byte. Alternatives considered:
#   - pip install -r requirements.txt: requires maintaining a separate export
#     step; one more place for drift.
#   - pip install .: ignores uv.lock, re-resolves transitive deps against
#     latest PyPI metadata on every build -> non-reproducible.
FROM public.ecr.aws/lambda/python:3.12

# ---------------------------------------------------------------------------
# OS deps
# ---------------------------------------------------------------------------
# dnf (AL2023) over yum. poppler-utils provides pdftoppm/pdftocairo/pdftotext
# that pdf2image + MarkItDown depend on. --setopt=install_weak_deps=0 keeps
# the image from pulling recommended-but-unused packages (AL2023 libdnf5 parses
# this as an int/bool — "False" with a capital F is rejected).
RUN dnf install -y --setopt=install_weak_deps=0 \
      poppler-utils \
      tar \
      gzip \
    && dnf clean all \
    && rm -rf /var/cache/dnf

# ---------------------------------------------------------------------------
# Python deps via uv
# ---------------------------------------------------------------------------
# Install uv into a stable path; the base image ships with pip but not uv.
# We use `uv pip install --system` (not `uv sync`) here — `uv sync` creates a
# .venv/ that Lambda doesn't know to activate. --system installs straight into
# the Lambda runtime's /var/lang/lib/python3.12/site-packages, which is the
# Python the CMD handler resolves against.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /uvx /usr/local/bin/

# Copy dependency manifests first so Docker layer cache only busts when
# pyproject.toml / uv.lock change — not on every src/ edit.
WORKDIR ${LAMBDA_TASK_ROOT}
COPY pyproject.toml uv.lock ./

# Export the locked dependency graph to a requirements.txt and install into
# the system interpreter. Why export then install, rather than
# `uv pip install --system .`:
#   - `uv pip install .` triggers a hatchling build of the project, which
#     validates `readme = "../README.md"` from pyproject.toml. The docker
#     build context is `backend/`, so "../README.md" resolves outside the
#     context and build fails. Only deps matter inside the image — the
#     project's own source is made importable via PYTHONPATH below.
#   - `uv export` resolves against uv.lock (reproducible) rather than
#     re-resolving against PyPI. Matches local dev byte-for-byte.
# --no-dev drops pytest / ruff / mypy / ragas from the image.
# --no-emit-project skips emitting the project root itself (we install its
# source via PYTHONPATH, not as a wheel).
RUN uv export --frozen --no-dev --no-emit-project --format requirements-txt \
      --output-file /tmp/requirements.txt \
    && uv pip install --system --no-cache -r /tmp/requirements.txt \
    && rm -f /tmp/requirements.txt

# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------
# Copy application source. Putting this after the deps install keeps the big
# dependency layer cached when only application code changes.
COPY src/ ./src/
COPY lambda_handler.py ./

# Put the dossier package on the import path without building a wheel.
# LAMBDA_TASK_ROOT is on sys.path by default; PYTHONPATH extension makes
# `src/dossier/...` resolve as `dossier...`. Equivalent to pip-installing
# an editable package, minus the hatchling build step that reads ../README.md.
ENV PYTHONPATH=${LAMBDA_TASK_ROOT}/src

# lambda_handler.py sits at ${LAMBDA_TASK_ROOT}; importable as
# `lambda_handler` for the CMD below.
CMD ["lambda_handler.handler"]
