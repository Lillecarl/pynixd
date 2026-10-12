/*
 * pynixd-agent: host samples for pynixd, over one SSH exec channel. Issue #98.
 *
 * usage: pynixd-agent INTERVAL_MS [RESOURCE:KIND:STALL_US:WINDOW_US]...
 *
 * Writes one frame per wake to stdout and exits at EOF on stdin, so the
 * channel's end is the agent's end. It wakes every INTERVAL_MS, at once when
 * a PSI trigger named on the command line fires, and at once for a line on
 * stdin.
 *
 * Output: `pynixd-agent 1 <cpus>\n`, then frames:
 *   F <path> <length>\n<bytes>    once per readable file
 *   E <reason> <uptime_ms>\n      reason: tick, ask, or the resource that fired
 *
 * The agent parses nothing. pynixd parses the files with the code that also
 * reads them over SFTP and locally.
 *
 * A trigger fd always polls readable (`DEFAULT_POLLMASK` in
 * `psi_trigger_poll`); only POLLPRI says it fired. Waiting for POLLIN on one
 * wakes on every iteration.
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define MAX_TRIGGERS 6
#define FILE_MAX 65536

/* `SAMPLED_FILES` of pynixd/monitor.py. */
static const char *const FILES[] = {
    "/proc/pressure/cpu", "/proc/pressure/memory", "/proc/pressure/io",
    "/proc/meminfo",      "/proc/loadavg",         "/sys/fs/cgroup/cpu.stat",
};

static char buf[FILE_MAX];

static int write_all(const char *data, size_t len) {
    while (len > 0) {
        ssize_t n = write(STDOUT_FILENO, data, len);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        data += n;
        len -= (size_t)n;
    }
    return 0;
}

static int emit_file(const char *path) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return 0;
    size_t len = 0;
    for (;;) {
        ssize_t n = read(fd, buf + len, sizeof buf - len);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            break;
        len += (size_t)n;
        if (len == sizeof buf)
            break;
    }
    close(fd);
    char head[256];
    int h = snprintf(head, sizeof head, "F %s %zu\n", path, len);
    if (write_all(head, (size_t)h) < 0 || write_all(buf, len) < 0)
        return -1;
    return 0;
}

static long long uptime_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static int emit_frame(const char *reason) {
    for (size_t i = 0; i < sizeof FILES / sizeof FILES[0]; i++)
        if (emit_file(FILES[i]) < 0)
            return -1;
    char tail[128];
    int t = snprintf(tail, sizeof tail, "E %s %lld\n", reason, uptime_ms());
    return write_all(tail, (size_t)t);
}

/* "memory:full:100000:2000000" -> an armed trigger fd, or -1. */
static int arm(const char *spec, char *resource, size_t resource_len) {
    char kind[8];
    unsigned long stall, window;
    if (sscanf(spec, "%15[a-z]:%7[a-z]:%lu:%lu", resource, kind, &stall, &window) != 4)
        return -1;
    resource[resource_len - 1] = '\0';
    char path[64];
    snprintf(path, sizeof path, "/proc/pressure/%s", resource);
    int fd = open(path, O_RDWR | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0)
        return -1;
    char line[64];
    int n = snprintf(line, sizeof line, "%s %lu %lu", kind, stall, window);
    if (write(fd, line, (size_t)n + 1) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s INTERVAL_MS [RESOURCE:KIND:STALL_US:WINDOW_US]...\n", argv[0]);
        return 2;
    }
    int interval = atoi(argv[1]);
    if (interval < 100)
        interval = 100;

    struct pollfd fds[1 + MAX_TRIGGERS];
    char names[1 + MAX_TRIGGERS][16];
    nfds_t nfds = 1;
    fds[0] = (struct pollfd){.fd = STDIN_FILENO, .events = POLLIN};
    for (int i = 2; i < argc && nfds < 1 + MAX_TRIGGERS; i++) {
        int fd = arm(argv[i], names[nfds], sizeof names[nfds]);
        if (fd < 0) {
            fprintf(stderr, "pynixd-agent: trigger %s: %s\n", argv[i], strerror(errno));
            continue;
        }
        fds[nfds] = (struct pollfd){.fd = fd, .events = POLLPRI};
        nfds++;
    }

    char hello[64];
    int h = snprintf(hello, sizeof hello, "pynixd-agent 1 %ld\n", sysconf(_SC_NPROCESSORS_ONLN));
    if (write_all(hello, (size_t)h) < 0 || emit_frame("tick") < 0)
        return 1;

    long long next = uptime_ms() + interval;
    for (;;) {
        long long wait = next - uptime_ms();
        int n = poll(fds, nfds, wait > 0 ? (int)wait : 0);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return 1;
        }
        const char *reason = NULL;
        if (fds[0].revents) {
            ssize_t r = read(STDIN_FILENO, buf, sizeof buf);
            if (r <= 0)
                return 0;
            reason = "ask";
        }
        for (nfds_t i = 1; i < nfds; i++) {
            if (fds[i].revents & POLLERR)
                return 1;
            if (fds[i].revents & POLLPRI)
                reason = names[i];
        }
        if (reason == NULL && uptime_ms() < next)
            continue;
        if (emit_frame(reason ? reason : "tick") < 0)
            return 1;
        next = uptime_ms() + interval;
    }
}
