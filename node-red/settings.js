module.exports = {
    flowFile: "/opt/openscan3/node-red/node-red.json",
    uiPort: process.env.PORT || 1880,
    uiHost: "127.0.0.1",
    // Serve the editor at /nodered so root can redirect to /dashboard safely
    httpAdminRoot: "/nodered",
    // Keep HTTP In/Out nodes at root (default). Adjust if you need a subpath.
    httpNodeRoot: "/",
    diagnostics: { enabled: false },
    enableEditor: true,
    httpNodeAuth: null,
    httpStaticAuth: null,
    editorTheme: {},
    functionGlobalContext: {}
};
