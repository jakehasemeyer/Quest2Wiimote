// App-local XInput rumble adapter for bundled Dolphin; no kernel driver.
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <windows.h>
#include <xinput.h>
#include <string.h>

static INIT_ONCE once = INIT_ONCE_STATIC_INIT;
static HMODULE real;
static SRWLOCK lock = SRWLOCK_INIT;
static SOCKET output = INVALID_SOCKET;
static XINPUT_VIBRATION motors[2];
static ULONGLONG last_sent[2];
static BOOL enabled = TRUE;

static BOOL CALLBACK initialize(PINIT_ONCE init, PVOID parameter, PVOID *context) {
    WCHAR path[MAX_PATH];
    GetSystemDirectoryW(path, MAX_PATH);
    lstrcatW(path, L"\\xinput1_4.dll");
    real = LoadLibraryW(path);
    return TRUE;
}

static FARPROC original(const char *name) {
    InitOnceExecuteOnce(&once, initialize, NULL, NULL);
    return real ? GetProcAddress(real, name) : NULL;
}

static void send_rumble(DWORD slot, BOOL force) {
    AcquireSRWLockExclusive(&lock);
    ULONGLONG now = GetTickCount64();
    if (force || now-last_sent[slot] >= 100) {
        if (output == INVALID_SOCKET) {
            WSADATA wsa;
            if (!WSAStartup(MAKEWORD(2,2), &wsa)) {
                output = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
                u_long nonblocking = 1; ioctlsocket(output, FIONBIO, &nonblocking);
            }
        }
        if (output != INVALID_SOCKET) {
            struct sockaddr_in target = {0};
            target.sin_family = AF_INET; target.sin_port = htons(26762);
            target.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            unsigned char packet[9] = {'Q','2','R','H', (unsigned char)slot};
            memcpy(packet+5, &motors[slot], 4);
            sendto(output, (const char *)packet, sizeof(packet), 0, (struct sockaddr *)&target, sizeof(target));
        }
        last_sent[slot] = now;
    }
    ReleaseSRWLockExclusive(&lock);
}

// Slots 2/3 are Quest right/left output devices inside this Dolphin only.
DWORD WINAPI BridgeGetState(DWORD index, XINPUT_STATE *state) {
    if (index == 2 || index == 3) {
        if (!state) return ERROR_BAD_ARGUMENTS;
        ZeroMemory(state, sizeof(*state)); state->dwPacketNumber = 1;
        send_rumble(index-2, FALSE);
        return ERROR_SUCCESS;
    }
    typedef DWORD (WINAPI *Function)(DWORD, XINPUT_STATE *);
    Function fn = (Function)original("XInputGetState");
    return fn ? fn(index, state) : ERROR_DEVICE_NOT_CONNECTED;
}

DWORD WINAPI BridgeSetState(DWORD index, XINPUT_VIBRATION *value) {
    if (index == 2 || index == 3) {
        if (!value) return ERROR_BAD_ARGUMENTS;
        AcquireSRWLockExclusive(&lock);
        motors[index-2] = enabled ? *value : (XINPUT_VIBRATION){0};
        ReleaseSRWLockExclusive(&lock);
        send_rumble(index-2, TRUE);
        return ERROR_SUCCESS;
    }
    typedef DWORD (WINAPI *Function)(DWORD, XINPUT_VIBRATION *);
    Function fn = (Function)original("XInputSetState");
    return fn ? fn(index, value) : ERROR_DEVICE_NOT_CONNECTED;
}

DWORD WINAPI BridgeGetCapabilities(DWORD index, DWORD flags, XINPUT_CAPABILITIES *caps) {
    if (index == 2 || index == 3) {
        if (!caps) return ERROR_BAD_ARGUMENTS;
        ZeroMemory(caps, sizeof(*caps)); caps->Type = XINPUT_DEVTYPE_GAMEPAD;
        caps->SubType = XINPUT_DEVSUBTYPE_GAMEPAD; caps->Flags = XINPUT_CAPS_FFB_SUPPORTED;
        caps->Vibration.wLeftMotorSpeed = caps->Vibration.wRightMotorSpeed = 65535;
        return ERROR_SUCCESS;
    }
    typedef DWORD (WINAPI *Function)(DWORD, DWORD, XINPUT_CAPABILITIES *);
    Function fn = (Function)original("XInputGetCapabilities");
    return fn ? fn(index, flags, caps) : ERROR_DEVICE_NOT_CONNECTED;
}

DWORD WINAPI BridgeGetBatteryInformation(DWORD index, BYTE type, XINPUT_BATTERY_INFORMATION *battery) {
    if (index == 2 || index == 3) {
        if (!battery) return ERROR_BAD_ARGUMENTS;
        battery->BatteryType = BATTERY_TYPE_WIRED; battery->BatteryLevel = BATTERY_LEVEL_FULL;
        return ERROR_SUCCESS;
    }
    typedef DWORD (WINAPI *Function)(DWORD, BYTE, XINPUT_BATTERY_INFORMATION *);
    Function fn = (Function)original("XInputGetBatteryInformation");
    return fn ? fn(index, type, battery) : ERROR_DEVICE_NOT_CONNECTED;
}

void WINAPI BridgeEnable(BOOL value) {
    AcquireSRWLockExclusive(&lock); enabled = value;
    if (!value) ZeroMemory(motors, sizeof(motors));
    ReleaseSRWLockExclusive(&lock);
    if (!value) { send_rumble(0, TRUE); send_rumble(1, TRUE); }
    typedef void (WINAPI *Function)(BOOL);
    Function fn = (Function)original("XInputEnable");
    if (fn) fn(value);
}

DWORD WINAPI BridgeGetKeystroke(DWORD index, DWORD flags, PXINPUT_KEYSTROKE stroke) {
    if (index == 2 || index == 3) return ERROR_EMPTY;
    typedef DWORD (WINAPI *Function)(DWORD, DWORD, PXINPUT_KEYSTROKE);
    Function fn = (Function)original("XInputGetKeystroke");
    return fn ? fn(index, flags, stroke) : ERROR_DEVICE_NOT_CONNECTED;
}

DWORD WINAPI BridgeGetAudioDeviceIds(DWORD index, LPWSTR render, UINT *render_count, LPWSTR capture, UINT *capture_count) {
    if (index == 2 || index == 3) return ERROR_DEVICE_NOT_CONNECTED;
    typedef DWORD (WINAPI *Function)(DWORD, LPWSTR, UINT *, LPWSTR, UINT *);
    Function fn = (Function)original("XInputGetAudioDeviceIds");
    return fn ? fn(index, render, render_count, capture, capture_count) : ERROR_DEVICE_NOT_CONNECTED;
}
