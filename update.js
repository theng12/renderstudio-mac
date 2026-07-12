module.exports = {
  run: [
    {
      when: "{{running('start.js')}}",
      method: "script.stop",
      params: { uri: "start.js" }
    },
    {
      when: "{{exists('.git')}}",
      method: "shell.run",
      params: { message: "git pull" }
    },
    {
      method: "shell.run",
      params: {
        message: [
          "if [ -x /opt/homebrew/bin/brew ]; then /opt/homebrew/bin/brew list ffmpeg >/dev/null 2>&1 || /opt/homebrew/bin/brew install ffmpeg; fi"
        ]
      }
    },
    {
      when: "{{exists('conda_env')}}",
      method: "shell.run",
      params: {
        path: "app",
        conda: { path: "{{path.resolve(cwd, 'conda_env')}}" },
        message: [
          "uv pip install -r requirements.txt",
          "if [ ! -x /opt/homebrew/bin/ffmpeg ]; then conda install -y -c conda-forge ffmpeg; fi"
        ]
      }
    },
    {
      when: "{{exists('service/.installed')}}",
      method: "shell.run",
      params: { message: "bash install_service.sh" }
    },
    {
      when: "{{!exists('service/.installed')}}",
      method: "script.start",
      params: { uri: "start.js" }
    }
  ]
}
