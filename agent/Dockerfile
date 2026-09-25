# syntax=docker/dockerfile:1
FROM golang:1.22-alpine AS builder
WORKDIR /build
COPY go.mod main.go ./
RUN CGO_ENABLED=0 go build -ldflags "-s -w" -o dns-agent .

FROM alpine:3.20
RUN apk add --no-cache \
    knot \
    pdns-tools \
    bind-tools \
    ca-certificates

COPY --from=builder /build/dns-agent /usr/local/bin/dns-agent
EXPOSE 8080
CMD ["dns-agent"]