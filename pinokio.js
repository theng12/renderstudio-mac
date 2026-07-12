module.exports = {
  version: "3.6",
  title: "Render Studio KH",
  description: "Episode-level FFmpeg render worker for the Story Studio fleet.",
  menu: async (kernel, info) => {
    if (info.running("install.js")) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Installing", href: "install.js" }]
    }
    if (!info.exists("conda_env")) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Install", href: "install.js" }]
    }
    if (info.running("start.js")) {
      const local = info.local("start.js")
      if (local && local.url) {
        return [
          { default: true, icon: "fa-solid fa-film", text: "Open Render Studio", href: `${local.url}/?_cb=${Date.now()}` },
          { icon: "fa-solid fa-terminal", text: "Worker Log", href: "start.js" },
          { icon: "fa-solid fa-folder-open", text: "Retained Videos", href: "data/outputs?fs=true" },
          { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" }
        ]
      }
      return [{ default: true, icon: "fa-solid fa-terminal", text: "Starting", href: "start.js" }]
    }
    return [
      { default: true, icon: "fa-solid fa-power-off", text: "Start", href: "start.js" },
      { icon: "fa-solid fa-folder-open", text: "Retained Videos", href: "data/outputs?fs=true" },
      { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" },
      { icon: "fa-solid fa-plug", text: "Reinstall", href: "install.js" },
      { icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" }
    ]
  }
}
