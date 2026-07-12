module.exports = {
  daemon: true,
  run: [
    {
      method: "shell.run",
      params: {
        path: "app",
        conda: { path: "{{path.resolve(cwd, 'conda_env')}}" },
        env: { PYTHONUNBUFFERED: "1" },
        message: [
          "if [ -f ../service/.installed ]; then echo \"Startup service mode is installed. Use 'Open UI (service)' or uninstall the startup service before using Start.\"; exit 1; fi",
          "python -m uvicorn backend.main:app --host 0.0.0.0 --port 47874"
        ],
        on: [{
          event: "/(http:\\/\\/[0-9.:]+)/",
          done: true
        }]
      }
    },
    {
      method: "local.set",
      params: { url: "{{input.event[1]}}" }
    }
  ]
}
