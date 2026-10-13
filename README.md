# SAITO Control — Render Deployment Package

This package contains the complete standalone Python bot backend with all Render configurations.

## 🚀 How to Deploy on Render

1. Create a new GitHub repository (e.g. `saito-render-bot`).
2. Push all files from this folder directly to that repository.
3. Log in to [Render Dashboard](https://dashboard.render.com).
4. Click **New +** -> **Web Service**.
5. Connect your GitHub repository.
6. Render will automatically detect the **Dockerfile**.
7. Configuration:
   - **Environment**: Docker
   - **Region**: Singapore (Closest to Garena Asian servers)
   - **Plan**: Starter ($7/month recommended for continuous UDP socket loops)
   - **Health Check Path**: `/api/stats`
8. Click **Deploy Web Service**.

Once deployed, copy your Render URL (e.g. `https://saito-freefire-bot.onrender.com`) and paste it into your Cloudflare dashboard at `https://trefguu.pages.dev/`!
