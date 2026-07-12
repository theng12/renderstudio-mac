module.exports = {
  requires: { bundle: "ai" },
  run: [
    {
      method: "shell.run",
      params: {
        message: [
          "if [ -x /opt/homebrew/bin/brew ]; then /opt/homebrew/bin/brew list ffmpeg >/dev/null 2>&1 || /opt/homebrew/bin/brew install ffmpeg; else echo 'System Homebrew not found; installing the CPU FFmpeg fallback in Render Studio.'; fi"
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        path: "app",
        conda: {
          path: "{{path.resolve(cwd, 'conda_env')}}",
          python: "python=3.12"
        },
        message: [
          "python -m pip install --upgrade pip",
          "uv pip install -r requirements.txt",
          "if [ ! -x /opt/homebrew/bin/ffmpeg ]; then conda install -y -c conda-forge ffmpeg; fi"
        ]
      }
    }
  ]
}
