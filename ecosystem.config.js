module.exports = {
  apps: [
    {
      name: 'silkroad-whatsapp-worker',
      script: 'worker/index.js',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};
