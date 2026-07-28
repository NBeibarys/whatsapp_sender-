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
    {
      name: 'silkroad-web',
      script: '.venv/bin/uvicorn',
      args: 'web.main:app --host 127.0.0.1 --port 8501',
      interpreter: 'none',
      cwd: __dirname,
      autorestart: true,
      max_restarts: 10,
      restart_delay: 5000,
    },
  ],
};
