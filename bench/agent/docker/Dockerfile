# Disposable run environment for the agent-loop eval (roadmap M5.7.b, pre-registration §5.1).
# Agents run with bypassPermissions, so they run here — never on an operator's machine.
# Toolchains per the pre-registration: JDK 17, .NET 9 SDK, Node 20; plus git, Python and the agent CLIs.
FROM mcr.microsoft.com/dotnet/sdk:9.0-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1 \
    UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/python VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:/usr/local/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless git ca-certificates curl gnupg jq procps build-essential libsqlite3-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# .NET 8 runtimes next to the 9 SDK: three eShop task bases target net8.0, and their unit tests run on it
RUN curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 8.0 --runtime dotnet --install-dir /usr/share/dotnet \
    && bash /tmp/dotnet-install.sh --channel 8.0 --runtime aspnetcore --install-dir /usr/share/dotnet \
    && rm /tmp/dotnet-install.sh

# uv-managed Python 3.12 + a venv outside the mounted checkout (the host .venv is macOS-only)
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
RUN uv venv --python 3.12 /opt/venv

# agent CLIs (versions recorded per run by the harness)
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force

# ai-discovery's dependencies at build time; the checkout itself is installed editable at start (run.sh)
COPY pyproject.toml /tmp/ai-discovery/pyproject.toml
COPY src/ai_discovery/__init__.py /tmp/ai-discovery/src/ai_discovery/__init__.py
RUN uv pip install /tmp/ai-discovery && uv pip uninstall ai-discovery && rm -rf /tmp/ai-discovery

RUN useradd -m -u 1000 bench && mkdir -p /home/bench/.cache/uv && chown -R bench:bench /opt/venv /home/bench/.cache && git config --system user.name bench && git config --system user.email bench@localhost \
    && git config --system --add safe.directory '*'
USER bench
WORKDIR /code-intel/ai-discovery
