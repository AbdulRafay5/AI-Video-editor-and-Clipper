# Deploy the Clipper website and run the app locally

The Vercel site is a public download and setup page. The full Flask app does
not run on Vercel: each user runs the app and its processing on their own
computer. This keeps videos and generated clips on that user's device and avoids
the cost of a shared processing server.

## Deploy the download site to Vercel

1. Push this repository to GitHub. The repository must be public for visitors
   to download the ZIP without signing in.
2. In Vercel, import the GitHub repository as a new project.
3. Set **Root Directory** to `vercel-site`.
4. Select the **Other** framework preset. Leave the build command and output
   directory empty.
5. Deploy. The Vercel URL serves the static download and setup page.

The Vercel page links to the source ZIP on the `main` branch. If the repository
or default branch changes, update the links in `vercel-site/index.html`.

Vercel hosts only this static page. It does not run the app, store user videos,
or process them.

## Run the app on each user's computer

### Requirements

- Windows with Docker Desktop and WSL 2, macOS with Docker Desktop, or Linux
  with Docker Engine and the Docker Compose plugin.
- A modern 64-bit computer. CPU processing works, but transcription and video
  encoding can be slow. Allow several GB for Docker, the Ollama language model,
  and the speech model, plus additional disk space for source videos and clips.
- Internet access for the initial container and AI model downloads.

### Start the app

1. Install and start Docker Desktop (Windows/macOS) or Docker Engine with the
   Compose plugin (Linux).
2. Download the source ZIP from the Vercel page and extract it.
3. Open PowerShell or a terminal in the extracted project folder.
4. Start the app and its private Ollama service:

   ```powershell
   docker compose up --build -d
   ```

5. Download the default language model (one-time download):

   ```powershell
   docker compose exec ollama ollama pull qwen3:4b
   ```

6. Open **http://localhost:5000**.
7. Upload a short test video. The first transcription downloads the speech model
   and may take a while; keep Docker running while a job is processing.

### Data, updates, and stop

- The app's database, videos, generated clips, settings, and speech models are
  stored in the local `data/` folder. Back it up if the user wants to keep them.
- Ollama's language model is stored in Docker's `ollama-data` volume.
- Update the source code, then run `docker compose up --build -d` again.
- Stop the services with `docker compose down`. This keeps data. To remove
  everything, users can remove the `data/` folder and the `ollama-data` Docker
  volume; that permanently deletes their projects and downloaded model.

### Important

- The app is bound to `127.0.0.1`, so it is only available on that computer by
  default. Do not change the port binding to `0.0.0.0` or expose it to the
  internet without adding authentication and security protections: the app has
  no user accounts, and anyone who can reach it could access its projects.
- Users need enough free disk space for their videos, clips, and downloaded
  models. The app's upload-size setting does not override Docker, filesystem,
  or available-disk limits.
- This Compose setup uses CPU inference and encoding for broad compatibility.
  Transcription and video rendering still work without a GPU, but may be slow.
- Ollama and the app run as separate local containers. The Ollama port is only
  exposed inside the Compose network, not to the host or internet.
- The application is not multi-user. Each person should run their own
  instance; do not point multiple people at one shared instance.
