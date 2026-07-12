module.exports = {
  version: "3.6",
  title: "Render Studio KH",
  description: "Episode-level FFmpeg render worker for the Story Studio fleet.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    const serviceInstalled = info.exists("service/.installed")
    const serviceUrl = "http://localhost:47874"
    if (info.running("install.js")) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Installing", href: "install.js" }]
    }
    if (!info.exists("conda_env")) {
      return [{ default: true, icon: "fa-solid fa-plug", text: "Install", href: "install.js" }]
    }
    if (serviceInstalled) {
      return [
        { default: true, icon: "fa-solid fa-film", text: "Open UI (service)", href: `${serviceUrl}/?_cb=${Date.now()}` },
        { icon: "fa-solid fa-stethoscope", text: "Check Service Status", href: "service_status.js" },
        { icon: "fa-solid fa-rotate-right", text: "Restart Service", href: "service_restart.js" },
        { icon: "fa-solid fa-screwdriver-wrench", text: "Repair Startup Service", href: "service.js" },
        { icon: "fa-solid fa-folder-open", text: "Service Logs", href: "logs/service?fs=true" },
        { icon: "fa-solid fa-folder-open", text: "Retained Videos", href: "data/outputs?fs=true" },
        { icon: "fa-regular fa-circle-xmark", text: "Uninstall Startup Service", href: "unservice.js" },
        { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" }
      ]
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
      { icon: "fa-solid fa-heart-pulse", text: "Install as Startup Service", href: "service.js" },
      { icon: "fa-solid fa-folder-open", text: "Retained Videos", href: "data/outputs?fs=true" },
      { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" },
      { icon: "fa-solid fa-plug", text: "Reinstall", href: "install.js" },
      { icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" }
    ]
  }
}
