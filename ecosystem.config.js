const { apps } = require("./ecosystem.config");

module.exports = {
    apps: [
        {
            name: "aichat.by",
            script: "server.py",
        },
    ],
};
