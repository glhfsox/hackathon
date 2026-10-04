# Built from the repository root (see compose.yaml).
FROM node:22-slim

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
# Vite reads VITE_* at build time; the backend URL stays the default http://localhost:8000.
# The playground sends VITE_PLAYGROUND_API_KEY as its Bearer token, so it gets a JWT (HS256,
# role developer, valid 30 days) signed here with the layer's JWT_SECRET.
ARG JWT_SECRET
ARG VITE_PLAYGROUND_MODEL=gpt-4o-mini
ENV VITE_PLAYGROUND_MODEL=$VITE_PLAYGROUND_MODEL
RUN VITE_PLAYGROUND_API_KEY=$(node -e ' \
      const c = require("crypto"); \
      const b = (o) => Buffer.from(JSON.stringify(o)).toString("base64url"); \
      const exp = Math.floor(Date.now() / 1000) + 30 * 86400; \
      const m = b({alg: "HS256", typ: "JWT"}) + "." + b({sub: "playground", roles: ["developer"], exp}); \
      console.log(m + "." + c.createHmac("sha256", process.env.JWT_SECRET).update(m).digest("base64url"));') \
    npm run build

EXPOSE 5173
# Port 5173 is the origin the backend allows for CORS.
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173", "--strictPort"]
