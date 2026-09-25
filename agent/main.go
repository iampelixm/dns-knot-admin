package main

import (
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"time"
)

var (
	version   = "0.2.0"
	startTime = time.Now()
)

type ApplyRequest struct {
	Config         string   `json:"config"`
	ConfigPath     string   `json:"config_path"`
	SetupCommands  []string `json:"setup_commands"`
	PostCommands   []string `json:"post_commands"`
}

type ApplyResponse struct {
	Ok      bool              `json:"ok"`
	Message string            `json:"message"`
	Serials map[string]int64  `json:"serials,omitempty"`
}

type ZoneStatus struct {
	Name   string `json:"name"`
	Serial int64  `json:"serial"`
	Status string `json:"status"`
}

type StatusResponse struct {
	Alive   bool         `json:"alive"`
	PID     int          `json:"pid"`
	Version string       `json:"version"`
	Uptime  string       `json:"uptime"`
	Zones   []ZoneStatus `json:"zones,omitempty"`
}

func getPID() int {
	return os.Getpid()
}

func isAlive() bool {
	return true
}

func apiKeyMiddleware(next http.Handler) http.Handler {
	key := os.Getenv("AGENT_API_KEY")
	if key == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		got := r.Header.Get("X-API-Key")
		if got != key {
			http.Error(w, `{"ok":false,"message":"unauthorized"}`, http.StatusUnauthorized)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func writeJSON(w http.ResponseWriter, status int, v interface{}) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func runCommands(commands []string) error {
	for _, cmdLine := range commands {
		parts := strings.Fields(cmdLine)
		if len(parts) == 0 {
			continue
		}
		cmd := exec.Command(parts[0], parts[1:]...)
		if out, err := cmd.CombinedOutput(); err != nil {
			return fmt.Errorf("%s: %s", cmdLine, strings.TrimSpace(string(out)))
		}
	}
	return nil
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
	resp := StatusResponse{
		Alive:   isAlive(),
		PID:     getPID(),
		Version: version,
		Uptime:  time.Since(startTime).Round(time.Second).String(),
	}
	writeJSON(w, http.StatusOK, resp)
}

func handleApply(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, ApplyResponse{Ok: false, Message: "only POST allowed"})
		return
	}

	var req ApplyRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, ApplyResponse{Ok: false, Message: "bad JSON: " + err.Error()})
		return
	}

	if req.ConfigPath == "" {
		req.ConfigPath = "/etc/knot/knot.conf"
	}

	// 1. setup commands (import TSIG keys, create zones, etc.)
	if err := runCommands(req.SetupCommands); err != nil {
		writeJSON(w, http.StatusInternalServerError, ApplyResponse{Ok: false, Message: "setup: " + err.Error()})
		return
	}

	// 2. write config
	cfgDir := req.ConfigPath[:strings.LastIndex(req.ConfigPath, "/")]
	if err := os.MkdirAll(cfgDir, 0755); err != nil {
		writeJSON(w, http.StatusInternalServerError, ApplyResponse{Ok: false, Message: "mkdir: " + err.Error()})
		return
	}
	if err := os.WriteFile(req.ConfigPath, []byte(req.Config), 0644); err != nil {
		writeJSON(w, http.StatusInternalServerError, ApplyResponse{Ok: false, Message: "write: " + err.Error()})
		return
	}

	// 3. post commands (conf-check, reload, restart, etc.)
	if err := runCommands(req.PostCommands); err != nil {
		writeJSON(w, http.StatusInternalServerError, ApplyResponse{Ok: false, Message: "post: " + err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, ApplyResponse{Ok: true, Message: "config applied and reloaded"})
}

func handleReload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, ApplyResponse{Ok: false, Message: "only POST allowed"})
		return
	}

	reloadCmd := r.URL.Query().Get("cmd")
	if reloadCmd == "" {
		reloadCmd = "knotc reload"
	}

	parts := strings.Fields(reloadCmd)
	cmd := exec.Command(parts[0], parts[1:]...)
	if out, err := cmd.CombinedOutput(); err != nil {
		writeJSON(w, http.StatusInternalServerError, ApplyResponse{Ok: false, Message: "reload: " + string(out)})
		return
	}

	writeJSON(w, http.StatusOK, ApplyResponse{Ok: true, Message: "reloaded"})
}

func handleConfig(w http.ResponseWriter, r *http.Request) {
	configPath := r.URL.Query().Get("path")
	if configPath == "" {
		configPath = "/etc/knot/knot.conf"
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "config not found: " + err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"config": string(data)})
}

func main() {
	port := os.Getenv("AGENT_PORT")
	if port == "" {
		port = "8080"
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/status", handleStatus)
	mux.HandleFunc("/apply", handleApply)
	mux.HandleFunc("/reload", handleReload)
	mux.HandleFunc("/config", handleConfig)

	handler := apiKeyMiddleware(mux)

	ln, err := net.Listen("tcp", ":"+port)
	if err != nil {
		log.Fatalf("listen :%s: %v", port, err)
	}

	log.Printf("dns-agent v%s starting on :%s\n", version, port)
	if err := http.Serve(ln, handler); err != nil {
		log.Fatalf("serve: %v", err)
	}
}