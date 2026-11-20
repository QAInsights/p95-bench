# Running `p95-bench` v2 in Docker Sandboxes (`sbx`)

This folder sets up a clean Linux `amd64` Docker Sandbox (`shell` agent) to build the pinned JMeter/k6 worker images and run `p95-bench` v2 across multiple models using your own provider API keys (BYOK).

> **Note:** Docker Sandboxes provides the isolated Linux microVM and private Docker daemon. Model inference is billed separately through your own provider API keys. Running models sequentially in a single sandbox preserves the shared v2 \$30 budget ledger (`~/.local/state/p95-bench/p95-v2.0.0-budget.json`).

## 1. Choose your models & API keys (on your host)

1. Copy the example cohort file and uncomment the models you want to run:
   ```powershell
   Copy-Item sandbox/models.example.txt sandbox/models.txt
   ```
2. Create a git-ignored `sandbox/.env` file with only the provider keys you need:
   ```env
   OPENAI_API_KEY=...
   ANTHROPIC_API_KEY=...
   GEMINI_API_KEY=...
   DEEPSEEK_API_KEY=...
   MODEL_API_KEY=...
   MODELSCOPE_API_KEY=...
   TINKER_API_KEY=...
   ```
   *(Never commit `sandbox/.env`; it is listed in `.gitignore`.)*

## 2. Option A — Cloud Sandbox (recommended from Windows)

Create a Linux `amd64` cloud sandbox (4 vCPU / 8 GiB RAM is comfortable for building and running the JMeter/k6 worker containers):

```powershell
sbx login
sbx --cloud create --name p95-bench shell --platform linux/amd64 --cpus 4 --memory 8g --ttl 4h
```

Copy the repository into the sandbox and prepare the evaluator (zero API calls):

```powershell
sbx --cloud cp . shell/p95-bench:/home/agent/workspace/p95-bench
sbx --cloud exec -w /home/agent/workspace/p95-bench shell/p95-bench bash -lc "chmod +x sandbox/*.sh && ./sandbox/bootstrap.sh /home/agent/workspace/p95-bench"
```

Optional offline verification (runs Docker worker + native JMeter/k6 tests, zero API calls):

```powershell
sbx --cloud exec -w /home/agent/workspace/p95-bench -e VERIFY=1 shell/p95-bench ./sandbox/bootstrap.sh /home/agent/workspace/p95-bench
```

Dry-run your model cohort first to check keys and commands:

```powershell
sbx --cloud exec -w /home/agent/workspace/p95-bench --env-file sandbox/.env shell/p95-bench ./sandbox/run-models.sh --dry-run sandbox/models.txt
```

When ready to run the benchmarks:

```powershell
sbx --cloud exec -w /home/agent/workspace/p95-bench --env-file sandbox/.env shell/p95-bench ./sandbox/run-models.sh sandbox/models.txt
```

Copy the completed runs back to your machine, then stop or remove the cloud sandbox so compute billing stops:

```powershell
sbx --cloud cp shell/p95-bench:/home/agent/workspace/p95-bench/results/p95-v2.0.0 ./results/
sbx --cloud stop shell/p95-bench
# Or delete permanently once results are copied:
# sbx --cloud rm shell/p95-bench
```

## 3. Option B — Local Sandbox

Local sandboxes bind-mount your checkout directly and have no cloud compute cost:

```powershell
sbx create --name p95-bench shell . --cpus 4 --memory 8g
sbx exec p95-bench bash -lc "chmod +x sandbox/*.sh && ./sandbox/bootstrap.sh"
sbx exec --env-file sandbox/.env p95-bench ./sandbox/run-models.sh --dry-run sandbox/models.txt
sbx exec --env-file sandbox/.env p95-bench ./sandbox/run-models.sh sandbox/models.txt
```
