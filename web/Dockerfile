# 分离部署：node 构建 → nginx 静态（compose 里的 web 服务，开箱即用，无需本地先 build）
FROM node:20-slim AS webbuild
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . ./
RUN npm run build

FROM nginx:alpine
COPY --from=webbuild /build/dist/ /usr/share/nginx/html/
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
