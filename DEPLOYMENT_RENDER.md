# Deploying JARVIS to Render

This guide explains how to deploy the JARVIS Cloud Brain, PostgreSQL database, and Redis background workers to Render using the provided Blueprint.

## 🚀 One-Click Deployment

1.  **Push Code**: Ensure the `render.yaml` file is in your GitHub repository root.
2.  **Render Dashboard**: Go to [dashboard.render.com](https://dashboard.render.com).
3.  **Blueprints**: Click **"Blueprints"** in the top navigation.
4.  **Connect Repo**: Connect your `J.A.R.V.I.S` repository.
5.  **Approve**: Render will automatically detect the `render.yaml`. Click **"Apply"**.

## 🔑 Required Environment Variables

Once the blueprint starts deploying, you **MUST** go to the `jarvis-brain` and `jarvis-worker` settings to add your secrets (Render does not allow committing secrets in the blueprint):

| Variable | Description |
| :--- | :--- |
| `OPENAI_API_KEY` | Your OpenAI API key for planning and reasoning. |
| `ELEVENLABS_API_KEY` | (Optional) For voice synthesis. |
| `TWILIO_ACCOUNT_SID` | (Optional) For Twilio voice interactions. |
| `TWILIO_AUTH_TOKEN` | (Optional) |
| `AGENT_SECRET_TOKEN` | A strong random string for local agent authentication. |

## 🏗️ Architecture on Render

-   **Web Service (`jarvis-brain`)**: Runs the FastAPI app using Gunicorn and Uvicorn workers.
-   **Background Worker (`jarvis-worker`)**: Runs the `rq` worker to process goals and proactive tasks asynchronously.
-   **PostgreSQL**: Stores your persistent memory, execution states, and user sessions.
-   **Redis**: Acts as the message broker between the Cloud Brain and the Worker.

## 📡 Connecting your Local Agent

After deployment, your `JARVIS_CLOUD_URL` in your local `.env` should be:
`https://jarvis-brain.onrender.com` (replace with your actual Render URL).

## 🛠️ Troubleshooting

-   **Database Connections**: If the app fails to start, verify that the `DATABASE_URL` is being correctly injected by the blueprint.
-   **Memory Limits**: The Free tier on Render has 512MB RAM. If you observe crashes, consider moving to the **Starter** plan.
-   **Port**: JARVIS is configured to listen on port `8000` by default, but Render will automatically map it to the public URL.
