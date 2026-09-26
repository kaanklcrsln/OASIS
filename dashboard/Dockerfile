FROM nginx:1.27-alpine

# Rendered to /etc/nginx/conf.d/default.conf by the image's envsubst entrypoint
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template

COPY index.html script.js tkgm-cache.js tkgm-cache-worker.js logo.png /usr/share/nginx/html/
COPY icons /usr/share/nginx/html/icons
COPY data /usr/share/nginx/html/data

EXPOSE 80
