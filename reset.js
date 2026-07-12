module.exports = {
  run: [
    {
      method: "input",
      params: {
        title: "Reset Render Studio",
        description: "Remove installed dependencies. Render history and retained outputs stay safe.",
        type: "modal",
        form: [{ type: "checkbox", key: "confirmed", title: "Remove dependencies" }]
      }
    },
    {
      when: "{{input.confirmed}}",
      method: "fs.rm",
      params: { path: "conda_env" }
    }
  ]
}
