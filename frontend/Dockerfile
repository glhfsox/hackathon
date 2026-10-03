# Built from the repository root (see compose.yaml).
FROM node:22-slim

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Vite reads VITE_* at build time; the backend URL stays the default http://localhost:8000.
ARG VITE_PLAYGROUND_API_KEY=""
ENV VITE_PLAYGROUND_API_KEY=$VITE_PLAYGROUND_API_KEY
RUN npm run build

EXPOSE 5173
# Port 5173 is the origin the backend allows for CORS.
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
