# git_bot

A modern, production-ready AI agent built with **Google Agent Development Kit (ADK) 2.0**, managed by the **uv** package manager, containerized with **Docker**, and automated with **Gitea Actions CI/CD**.

The project is designed with best practices: modular structure, typed tools, automated testing, formatting/linting, multi-stage Docker builds, and an empty base agent ready for adding features.

---

## 📁 Project Structure

```text
git_bot/
├── .gitea/
│   └── workflows/
│       └── ci.yaml              # Gitea Actions CI/CD (Test, Lint & Docker Build)
├── git_bot/                     # Agent package
│   ├── __init__.py              # Package export (root_agent)
│   ├── agent.py                 # Core ADK 2.0 root_agent definition
│   ├── config.py                # Environment & model settings
│   └── tools/                   # Agent tools module
│       ├── __init__.py          # Exported tools
│       └── time_tool.py         # Time tool with timezone support
├── tests/                       # Automated test suite
│   ├── __init__.py
│   ├── test_agent.py            # Agent configuration tests
│   └── test_time_tool.py        # Tool unit tests
├── .dockerignore                # Excludes unwanted files from Docker context
├── .env.example                 # Template for environment variables
├── .gitignore                   # Git ignore for Python, uv, ADK & IDEs
├── .python-version              # Pinned Python version
├── Dockerfile                   # Multi-stage, non-root Docker build
├── main.py                      # Standalone entrypoint / inspection script
├── pyproject.toml               # Project metadata & dependencies (uv)
├── uv.lock                      # Locked dependencies
└── README.md
```

---

## 🚀 Quickstart

### 1. Prerequisites
Ensure you have [uv](https://docs.astral.sh/uv/) installed (Python 3.12, 3.13, or 3.14):
```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Install Dependencies
Synchronize the virtual environment and install all dependencies (including dev tools):
```bash
uv sync --dev
```

### 3. Environment Configuration
Copy the example `.env` file and provide your Gemini API key:
```bash
cp .env.example .env
```
Edit `.env` and set your key:
```env
GOOGLE_API_KEY="your-gemini-api-key-here"
MODEL_NAME="gemini-2.0-flash"
```

---

## 🤖 Running the Agent Locally

### A. ADK Interactive CLI
Run the agent directly in interactive conversation mode:
```bash
uv run adk run git_bot
```

### B. ADK Web Development UI
Launch the ADK visual web playground:
```bash
uv run adk web .
```

### C. Quick Tool & Configuration Test
Run the standalone runner to verify tool execution and agent setup:
```bash
uv run python main.py
```

---

## 🐳 Docker Deployment

The project includes an optimized, multi-stage [`Dockerfile`](file:///Users/fweihrauch/Documents/Code/git_bot/Dockerfile) based on `python:3.14-slim-bookworm` with `uv` layer caching and a non-root user for security.

### Build the Docker Image
```bash
docker build -t git-bot .
```

### Run the Container (Web UI + API Server)
```bash
docker run -p 8080:8080 --env-file .env git-bot
```
The agent UI will be accessible at `http://localhost:8080`.

### Run Interactive CLI Inside Container
```bash
docker run -it --env-file .env git-bot adk run git_bot
```

### Run Standalone Diagnostics Inside Container
```bash
docker run --env-file .env git-bot python main.py
```

---

## 🧩 Adding New Features & Tools

To add new capabilities to `git_bot`:

1. **Create a new tool file** under `git_bot/tools/`, for example `git_bot/tools/calculator.py`:
   ```python
   def calculate(expression: str) -> dict[str, str]:
       """Evaluates a basic math expression.

       Args:
           expression: The math expression to evaluate (e.g. '42 * 2').
       """
       # Your tool logic here
       return {"result": str(eval(expression))}
   ```

2. **Export the tool** in [git_bot/tools/__init__.py](file:///Users/fweihrauch/Documents/Code/git_bot/git_bot/tools/__init__.py):
   ```python
   from git_bot.tools.time_tool import get_current_time
   from git_bot.tools.calculator import calculate

   __all__ = ["get_current_time", "calculate"]
   ```

3. **Register the tool** in [git_bot/agent.py](file:///Users/fweihrauch/Documents/Code/git_bot/git_bot/agent.py):
   ```python
   from git_bot.tools import get_current_time, calculate

   root_agent = Agent(
       name=settings.agent_name,
       model=settings.model,
       description=settings.agent_description,
       instruction=settings.instruction,
       tools=[get_current_time, calculate],
   )
   ```

---

## 🧪 Testing & Quality Assurance

Run the test suite:
```bash
uv run pytest -v
```

Check linting and formatting with Ruff:
```bash
# Check formatting
uv run ruff format --check .

# Check linting
uv run ruff check .

# Auto-fix linting issues
uv run ruff check --fix .
```

---

## 🔄 Gitea CI/CD

The workflow in [.gitea/workflows/ci.yaml](file:///Users/fweihrauch/Documents/Code/git_bot/.gitea/workflows/ci.yaml) runs automatically on pushes and pull requests to `main` and `master`:
1. **Lint & Test Job**:
   - Multi-version test matrix against Python **3.12**, **3.13**, and **3.14**.
   - Ultra-fast dependency synchronization with `uv sync --dev`.
   - Code style and lint enforcement with `ruff`.
   - Test execution with `pytest`.
2. **Docker Build Job**:
   - Runs automatically after test pass.
   - Sets up Docker Buildx with GitHub Actions cache backend.
   - Validates multi-stage Dockerfile build.
