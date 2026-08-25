# Sentinel dashboard - metadata-only console.
# Two stages: build the standalone Next.js bundle, then serve it from a
# small runtime image.
FROM node:20-alpine AS builder

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --no-audit --no-fund

COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

RUN addgroup --system --gid 10003 dashboard \
 && adduser --system --uid 10003 --ingroup dashboard dashboard

COPY --from=builder --chown=dashboard:dashboard /app/public ./public
COPY --from=builder --chown=dashboard:dashboard /app/.next/standalone ./
COPY --from=builder --chown=dashboard:dashboard /app/.next/static ./.next/static

USER dashboard
EXPOSE 3000

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
    CMD wget --spider -q http://127.0.0.1:3000/login || exit 1

CMD ["node", "server.js"]
