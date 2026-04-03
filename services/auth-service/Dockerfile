FROM node:20-alpine
WORKDIR /app
COPY package.json ./
RUN npm install --omit=dev
COPY auth.js ./
EXPOSE 8080
CMD ["node", "auth.js"]
