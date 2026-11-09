// NativeActivity + OpenXR + GLES. No engine or Java application runtime needed.
#define XR_USE_PLATFORM_ANDROID
#define XR_USE_GRAPHICS_API_OPENGL_ES
#include <jni.h>
#include <android_native_app_glue.h>
#include <android/log.h>
#include <android/input.h>
#include <android/keycodes.h>
#include <android/window.h>
#include <EGL/egl.h>
#include <GLES3/gl3.h>
#include <openxr/openxr.h>
#include <openxr/openxr_platform.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <netinet/tcp.h>
#include <fcntl.h>
#include <unistd.h>
#include <poll.h>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>
#include <algorithm>
#include <cmath>

#define LOG(...) __android_log_print(ANDROID_LOG_INFO, "Quest2Wiimote", __VA_ARGS__)
static void check(XrResult r, const char* call) {
    if (XR_FAILED(r)) { LOG("%s: %d", call, r); throw std::runtime_error(call); }
}
#define XR(call) check(call, #call)

#include "controller_models.h"

struct Sender {
    int fd = -1;
    bool connecting = false;
    double retry = 0;
    std::string host;
    char feedback[48]{};
    size_t feedbackSize = 0;
    float amplitudes[2]{}; // Wire order: right, left.
    double lastFeedback = 0;
    bool passthroughRequested = false;
    bool handTrackingRequested = false;
    bool screenVisible = false;
    XrPosef screenPose{{0,0,0,1},{0,0,0}};
    XrVector2f screenSize{1.2f,.675f};
    float screenAspect = 16.f/9;
    uint32_t screenUpdates = 0;
    bool cursorVisible = false;
    float cursorU = .5f, cursorV = .5f, cursorRoll = 0;
    uint32_t cursorUpdates = 0;
    double lastCursor = 0;
    static double seconds() {
        return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count();
    }
    ~Sender() { if (fd >= 0) close(fd); }
    void drop() {
        if (fd >= 0) close(fd); fd = -1; connecting = false;
        feedbackSize = 0; amplitudes[0] = amplitudes[1] = 0; lastFeedback = 0;
        screenVisible = false;
        cursorVisible = false;
    }
    void receiveFeedback() {
        if (fd < 0 || connecting) return;
        for (int i = 0; i < 16; ++i) {
            size_t target=feedbackSize<4 ? 4 : (!memcmp(feedback,"Q2SC",4) ? 48 : !memcmp(feedback,"Q2CR",4) ? 20 : 12);
            ssize_t n = recv(fd, feedback+feedbackSize, target-feedbackSize, MSG_DONTWAIT);
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
            if (n < 0 && errno == EINTR) continue;
            if (n <= 0) { drop(); break; }
            feedbackSize += n;
            if (feedbackSize<4) continue;
            target=!memcmp(feedback,"Q2SC",4) ? 48 : !memcmp(feedback,"Q2CR",4) ? 20 : 12;
            if (feedbackSize!=target) continue;
            if (!memcmp(feedback,"Q2CR",4)) {
                float values[4];memcpy(values,feedback+4,sizeof(values));
                if (!std::all_of(values,values+4,[](float v){return std::isfinite(v);})) { drop();break; }
                cursorVisible=values[0]>.5f;cursorU=values[1];cursorV=values[2];cursorRoll=values[3];
                ++cursorUpdates;lastCursor=seconds();feedbackSize=0;continue;
            }
            if (!memcmp(feedback,"Q2SC",4)) {
                float values[11];memcpy(values,feedback+4,sizeof(values));
                if (!std::all_of(values,values+11,[](float v){return std::isfinite(v);}) || values[1]<=0 || values[2]<=0 || values[10]<=0) { drop();break; }
                float norm=0;for(int j=6;j<10;++j) norm+=values[j]*values[j];
                if(norm<.5f || norm>1.5f) { drop();break; }
                screenVisible=values[0]>.5f;screenSize={values[1],values[2]};
                screenPose.position={values[3],values[4],values[5]};
                norm=sqrtf(norm);screenPose.orientation={values[6]/norm,values[7]/norm,values[8]/norm,values[9]/norm};screenAspect=values[10];
                ++screenUpdates;
                LOG("Virtual screen visible=%d size=%.4fx%.4f position=%.4f,%.4f,%.4f orientation=%.5f,%.5f,%.5f,%.5f",screenVisible,screenSize.x,screenSize.y,screenPose.position.x,screenPose.position.y,screenPose.position.z,screenPose.orientation.x,screenPose.orientation.y,screenPose.orientation.z,screenPose.orientation.w);
                feedbackSize=0;continue;
            }
            float values[2]; memcpy(values, feedback+4, sizeof(values));
            if (!std::isfinite(values[0]) || !std::isfinite(values[1]) ||
                values[0] < 0 || values[0] > 1 || values[1] < 0 || values[1] > 1) { drop(); break; }
            if (!memcmp(feedback,"Q2HB",4)) { std::copy(values,values+2,amplitudes); lastFeedback=seconds(); }
            else if (!memcmp(feedback,"Q2PT",4)) passthroughRequested = values[0] > .5f;
            else if (!memcmp(feedback,"Q2HT",4)) handTrackingRequested = values[0] > .5f;
            else { drop(); break; }
            feedbackSize = 0;
        }
        if (seconds()-lastFeedback > .35) amplitudes[0] = amplitudes[1] = 0;
    }
    bool sendFrame(const char* data, size_t length) {
        double now = seconds();
        if (fd < 0 && now >= retry) {
            retry = now+2;
            fd = socket(AF_INET, SOCK_STREAM, 0);
            if (fd < 0) return false;
            fcntl(fd, F_SETFL, O_NONBLOCK);
            int one = 1;
            setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            sockaddr_in addr{}; addr.sin_family = AF_INET; addr.sin_port = htons(26761);
            if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) { drop(); return false; }
            int result = connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
            if (result < 0 && errno != EINPROGRESS) { drop(); return false; }
            connecting = result < 0;
        }
        if (fd < 0) return false;
        if (connecting) {
            pollfd p{fd, POLLOUT, 0};
            if (poll(&p, 1, 0) <= 0) return false;
            int error = 0; socklen_t size = sizeof(error);
            getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size);
            if (error) { drop(); return false; }
            connecting = false;
            LOG("Connected to desktop %s:26761", host.c_str());
        }
        // A partial frame closes the stream so queued old motion never builds latency.
        ssize_t sent = send(fd, data, length, MSG_NOSIGNAL);
        if (sent != static_cast<ssize_t>(length)) { drop(); return false; }
        return true;
    }
};

static std::string extra(android_app* app, const char* name, const char* fallback) {
    JNIEnv* env = nullptr;
    app->activity->vm->AttachCurrentThread(&env, nullptr);
    jobject activity = app->activity->clazz;
    jclass cls = env->GetObjectClass(activity);
    jobject intent = env->CallObjectMethod(activity, env->GetMethodID(cls, "getIntent", "()Landroid/content/Intent;"));
    jclass ic = env->GetObjectClass(intent);
    jstring key = env->NewStringUTF(name);
    jstring value = static_cast<jstring>(env->CallObjectMethod(intent, env->GetMethodID(ic, "getStringExtra", "(Ljava/lang/String;)Ljava/lang/String;"), key));
    std::string result = fallback;
    if (value) {
        const char* chars = env->GetStringUTFChars(value, nullptr);
        result = chars;
        env->ReleaseStringUTFChars(value, chars);
        env->DeleteLocalRef(value);
    }
    env->DeleteLocalRef(key); env->DeleteLocalRef(ic); env->DeleteLocalRef(intent); env->DeleteLocalRef(cls);
    return result;
}

struct Eye {
    XrSwapchain swapchain{};
    GLuint depth{};
    int width{}, height{};
    std::vector<XrSwapchainImageOpenGLESKHR> images;
};

struct Bridge {
    android_app* app;
    XrInstance instance{};
    XrSystemId system{};
    XrSession session{};
    XrSpace local{}, head{}, hands[2]{}, grips[2]{};
    XrActionSet actionSet{};
    XrAction pose{}, gripPose{}, trigger{}, squeeze{}, stick{}, primary{}, secondary{}, click{}, menu{}, vibration{};
    bool handActive[2]{}, inputFocused = false;
    XrHandTrackerEXT handTrackers[2]{};
    PFN_xrLocateHandJointsEXT locateHandJoints{};
    PFN_xrDestroyHandTrackerEXT destroyHandTracker{};
    bool handTrackingSupported = false, handAimSupported = false;
    bool modelActive[2]{};
    float lastAmplitude[2]{};
    double lastHaptic[2]{};
    XrPath paths[2]{};
    XrSessionState state = XR_SESSION_STATE_UNKNOWN;
    bool running = false, exit = false;
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    Eye eyes[2];
    Eye screenImages[2];
    ControllerModels controllerModels;
    XrSpaceLocation modelPoses[2]{{XR_TYPE_SPACE_LOCATION},{XR_TYPE_SPACE_LOCATION}};
    uint32_t modelFrames = 0;
    GLenum modelGLError = GL_NO_ERROR;
    bool screenRendered = false;
    bool cursorRendered = false;
    GLuint fbo{};
    Sender sender;
    std::string token;
    XrPassthroughFB passthrough{};
    XrPassthroughLayerFB passthroughLayer{};
    PFN_xrDestroyPassthroughFB destroyPassthrough{};
    PFN_xrDestroyPassthroughLayerFB destroyPassthroughLayer{};
    PFN_xrPassthroughStartFB startPassthrough{};
    PFN_xrPassthroughPauseFB pausePassthrough{};
    PFN_xrPassthroughLayerResumeFB resumePassthroughLayer{};
    PFN_xrPassthroughLayerPauseFB pausePassthroughLayer{};
    bool passthroughSupported = false, passthroughActive = false;
    XrResult passthroughResult = XR_ERROR_FEATURE_UNSUPPORTED;
    double lastPassthroughAttempt = -1;

    explicit Bridge(android_app* a): app(a) {}
    XrPath path(const char* value) { XrPath p{}; XR(xrStringToPath(instance, value, &p)); return p; }
    XrAction action(const char* name, XrActionType type, bool both = true) {
        XrActionCreateInfo info{XR_TYPE_ACTION_CREATE_INFO};
        snprintf(info.actionName, sizeof(info.actionName), "%s", name);
        snprintf(info.localizedActionName, sizeof(info.localizedActionName), "%s", name);
        info.actionType = type;
        if (both) { info.countSubactionPaths = 2; info.subactionPaths = paths; }
        XrAction a{}; XR(xrCreateAction(actionSet, &info, &a)); return a;
    }
    template<class F> F proc(const char* name) {
        PFN_xrVoidFunction p{}; XR(xrGetInstanceProcAddr(instance, name, &p)); return reinterpret_cast<F>(p);
    }
    void init() {
        sender.host = extra(app, "host", "127.0.0.1");
        sender.passthroughRequested = extra(app,"passthrough","false") == "true";
        sender.handTrackingRequested = extra(app,"hand_tracking","false") == "true";
        token = extra(app, "token", "");
        if (token.size() != 48 || token.find_first_not_of("0123456789abcdef") != std::string::npos)
            throw std::runtime_error("Launch from the desktop app to pair the sender");
        PFN_xrInitializeLoaderKHR initialize{};
        XR(xrGetInstanceProcAddr(XR_NULL_HANDLE, "xrInitializeLoaderKHR", reinterpret_cast<PFN_xrVoidFunction*>(&initialize)));
        XrLoaderInitInfoAndroidKHR loader{XR_TYPE_LOADER_INIT_INFO_ANDROID_KHR};
        loader.applicationVM = app->activity->vm; loader.applicationContext = app->activity->clazz;
        XR(initialize(reinterpret_cast<XrLoaderInitInfoBaseHeaderKHR*>(&loader)));
        uint32_t count = 0;
        XR(xrEnumerateInstanceExtensionProperties(nullptr, 0, &count, nullptr));
        std::vector<XrExtensionProperties> available(count, {XR_TYPE_EXTENSION_PROPERTIES});
        XR(xrEnumerateInstanceExtensionProperties(nullptr, count, &count, available.data()));
        bool hasPassthrough = std::any_of(available.begin(), available.end(), [](auto& e) { return strcmp(e.extensionName, XR_FB_PASSTHROUGH_EXTENSION_NAME) == 0; });
        std::vector<const char*> extensions{XR_KHR_ANDROID_CREATE_INSTANCE_EXTENSION_NAME, XR_KHR_OPENGL_ES_ENABLE_EXTENSION_NAME};
        bool usePassthrough = hasPassthrough;
        if (usePassthrough) extensions.push_back(XR_FB_PASSTHROUGH_EXTENSION_NAME);
        bool hasHands = std::any_of(available.begin(), available.end(), [](auto& e) { return strcmp(e.extensionName, XR_EXT_HAND_TRACKING_EXTENSION_NAME) == 0; });
        handAimSupported = hasHands && std::any_of(available.begin(), available.end(), [](auto& e) { return strcmp(e.extensionName, XR_FB_HAND_TRACKING_AIM_EXTENSION_NAME) == 0; });
        if (hasHands) extensions.push_back(XR_EXT_HAND_TRACKING_EXTENSION_NAME);
        if (handAimSupported) extensions.push_back(XR_FB_HAND_TRACKING_AIM_EXTENSION_NAME);
        XrInstanceCreateInfoAndroidKHR android{XR_TYPE_INSTANCE_CREATE_INFO_ANDROID_KHR};
        android.applicationVM = app->activity->vm; android.applicationActivity = app->activity->clazz;
        XrInstanceCreateInfo ci{XR_TYPE_INSTANCE_CREATE_INFO}; ci.next = &android;
        strcpy(ci.applicationInfo.applicationName, "Quest2Wiimote");
        ci.applicationInfo.applicationVersion = 1; ci.applicationInfo.apiVersion = XR_API_VERSION_1_0;
        ci.enabledExtensionCount = extensions.size(); ci.enabledExtensionNames = extensions.data();
        XR(xrCreateInstance(&ci, &instance));
        XrSystemGetInfo si{XR_TYPE_SYSTEM_GET_INFO}; si.formFactor = XR_FORM_FACTOR_HEAD_MOUNTED_DISPLAY;
        XR(xrGetSystem(instance, &si, &system));
        if (hasHands) {
            XrSystemHandTrackingPropertiesEXT hands{XR_TYPE_SYSTEM_HAND_TRACKING_PROPERTIES_EXT};
            XrSystemProperties properties{XR_TYPE_SYSTEM_PROPERTIES}; properties.next = &hands;
            handTrackingSupported = XR_SUCCEEDED(xrGetSystemProperties(instance,system,&properties)) && hands.supportsHandTracking;
        }
        if (hasPassthrough) {
            XrSystemPassthroughProperties2FB pass{XR_TYPE_SYSTEM_PASSTHROUGH_PROPERTIES2_FB};
            XrSystemProperties properties{XR_TYPE_SYSTEM_PROPERTIES}; properties.next = &pass;
            passthroughResult = xrGetSystemProperties(instance,system,&properties);
            passthroughSupported = XR_SUCCEEDED(passthroughResult) && (pass.capabilities & XR_PASSTHROUGH_CAPABILITY_BIT_FB);
            usePassthrough = passthroughSupported;
        }
        LOG("Passthrough extension=%d supported=%d requested=%d result=%d",hasPassthrough,passthroughSupported,sender.passthroughRequested,passthroughResult);
        XrGraphicsRequirementsOpenGLESKHR requirements{XR_TYPE_GRAPHICS_REQUIREMENTS_OPENGL_ES_KHR};
        XR(proc<PFN_xrGetOpenGLESGraphicsRequirementsKHR>("xrGetOpenGLESGraphicsRequirementsKHR")(instance, system, &requirements));
        display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
        if (!eglInitialize(display, nullptr, nullptr)) throw std::runtime_error("eglInitialize");
        EGLint attrs[]{EGL_RENDERABLE_TYPE, EGL_OPENGL_ES3_BIT, EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_ALPHA_SIZE, 8, EGL_NONE};
        EGLConfig config{}; EGLint configs = 0;
        eglChooseConfig(display, attrs, &config, 1, &configs);
        if (!configs) throw std::runtime_error("No GLES3 config");
        EGLint ctx[]{EGL_CONTEXT_CLIENT_VERSION, 3, EGL_NONE};
        context = eglCreateContext(display, config, EGL_NO_CONTEXT, ctx);
        EGLint pb[]{EGL_WIDTH, 16, EGL_HEIGHT, 16, EGL_NONE};
        surface = eglCreatePbufferSurface(display, config, pb);
        if (!eglMakeCurrent(display, surface, surface, context)) throw std::runtime_error("eglMakeCurrent");
        XrGraphicsBindingOpenGLESAndroidKHR binding{XR_TYPE_GRAPHICS_BINDING_OPENGL_ES_ANDROID_KHR};
        binding.display = display; binding.config = config; binding.context = context;
        XrSessionCreateInfo sci{XR_TYPE_SESSION_CREATE_INFO}; sci.next = &binding; sci.systemId = system;
        XR(xrCreateSession(instance, &sci, &session));
        if (handTrackingSupported) {
            auto create = proc<PFN_xrCreateHandTrackerEXT>("xrCreateHandTrackerEXT");
            locateHandJoints = proc<PFN_xrLocateHandJointsEXT>("xrLocateHandJointsEXT");
            destroyHandTracker = proc<PFN_xrDestroyHandTrackerEXT>("xrDestroyHandTrackerEXT");
            for (int h=0;h<2;++h) {
                XrHandTrackerCreateInfoEXT info{XR_TYPE_HAND_TRACKER_CREATE_INFO_EXT};
                info.hand = h ? XR_HAND_RIGHT_EXT : XR_HAND_LEFT_EXT; info.handJointSet = XR_HAND_JOINT_SET_DEFAULT_EXT;
                XrResult result = create(session,&info,&handTrackers[h]);
                if (XR_FAILED(result)) { handTrackingSupported=false; LOG("Hand tracker creation failed: %d",result); }
            }
        }
        LOG("Hand tracking supported=%d aim=%d",handTrackingSupported,handAimSupported);
        XrReferenceSpaceCreateInfo space{XR_TYPE_REFERENCE_SPACE_CREATE_INFO}; space.poseInReferenceSpace.orientation.w = 1;
        space.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_LOCAL; XR(xrCreateReferenceSpace(session, &space, &local));
        space.referenceSpaceType = XR_REFERENCE_SPACE_TYPE_VIEW; XR(xrCreateReferenceSpace(session, &space, &head));
        setupActions();
        uint32_t views = 0;
        XR(xrEnumerateViewConfigurationViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, 0, &views, nullptr));
        std::vector<XrViewConfigurationView> configViews(views, {XR_TYPE_VIEW_CONFIGURATION_VIEW});
        XR(xrEnumerateViewConfigurationViews(instance, system, XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO, views, &views, configViews.data()));
        uint32_t formatCount = 0;
        XR(xrEnumerateSwapchainFormats(session, 0, &formatCount, nullptr));
        std::vector<int64_t> formats(formatCount); XR(xrEnumerateSwapchainFormats(session, formatCount, &formatCount, formats.data()));
        int64_t format = std::find(formats.begin(), formats.end(), GL_SRGB8_ALPHA8) != formats.end() ? GL_SRGB8_ALPHA8 : GL_RGBA8;
        for (int i = 0; i < 4; i++) {
            auto& eye = i<2 ? eyes[i] : screenImages[i-2];
            eye.width = i<2 ? std::min(768u, configViews[i].recommendedImageRectWidth) : 512;
            eye.height = i<2 ? std::min(768u, configViews[i].recommendedImageRectHeight) : 512;
            XrSwapchainCreateInfo sc{XR_TYPE_SWAPCHAIN_CREATE_INFO};
            sc.usageFlags = XR_SWAPCHAIN_USAGE_COLOR_ATTACHMENT_BIT; sc.format = format; sc.sampleCount = 1;
            sc.width = eye.width; sc.height = eye.height; sc.faceCount = 1; sc.arraySize = 1; sc.mipCount = 1;
            XR(xrCreateSwapchain(session, &sc, &eye.swapchain));
            uint32_t n = 0; XR(xrEnumerateSwapchainImages(eye.swapchain, 0, &n, nullptr));
            eye.images.resize(n, {XR_TYPE_SWAPCHAIN_IMAGE_OPENGL_ES_KHR});
            XR(xrEnumerateSwapchainImages(eye.swapchain, n, &n, reinterpret_cast<XrSwapchainImageBaseHeader*>(eye.images.data())));
            if (i<2) {
                glGenRenderbuffers(1,&eye.depth);glBindRenderbuffer(GL_RENDERBUFFER,eye.depth);
                glRenderbufferStorage(GL_RENDERBUFFER,GL_DEPTH_COMPONENT24,eye.width,eye.height);
            }
        }
        glGenFramebuffers(1, &fbo);
        controllerModels.init(app->activity->assetManager);
        if (usePassthrough) {
            destroyPassthrough = proc<PFN_xrDestroyPassthroughFB>("xrDestroyPassthroughFB");
            destroyPassthroughLayer = proc<PFN_xrDestroyPassthroughLayerFB>("xrDestroyPassthroughLayerFB");
            startPassthrough = proc<PFN_xrPassthroughStartFB>("xrPassthroughStartFB");
            pausePassthrough = proc<PFN_xrPassthroughPauseFB>("xrPassthroughPauseFB");
            resumePassthroughLayer = proc<PFN_xrPassthroughLayerResumeFB>("xrPassthroughLayerResumeFB");
            pausePassthroughLayer = proc<PFN_xrPassthroughLayerPauseFB>("xrPassthroughLayerPauseFB");
            XrPassthroughCreateInfoFB pc{XR_TYPE_PASSTHROUGH_CREATE_INFO_FB};
            XrResult result = proc<PFN_xrCreatePassthroughFB>("xrCreatePassthroughFB")(session, &pc, &passthrough);
            if (XR_SUCCEEDED(result)) {
                XrPassthroughLayerCreateInfoFB lc{XR_TYPE_PASSTHROUGH_LAYER_CREATE_INFO_FB};
                lc.passthrough = passthrough; lc.purpose = XR_PASSTHROUGH_LAYER_PURPOSE_RECONSTRUCTION_FB;
                result = proc<PFN_xrCreatePassthroughLayerFB>("xrCreatePassthroughLayerFB")(session, &lc, &passthroughLayer);
            }
            if (XR_SUCCEEDED(result)) {
                XrPassthroughStyleFB style{XR_TYPE_PASSTHROUGH_STYLE_FB}; style.textureOpacityFactor = 1.f;
                result = proc<PFN_xrPassthroughLayerSetStyleFB>("xrPassthroughLayerSetStyleFB")(passthroughLayer,&style);
            }
            passthroughResult = result;
            LOG("Passthrough resources result=%d",result);
        }
        LOG("Initialized, desktop %s:26761", sender.host.c_str());
    }
    void setupActions() {
        paths[0] = path("/user/hand/left"); paths[1] = path("/user/hand/right");
        XrActionSetCreateInfo set{XR_TYPE_ACTION_SET_CREATE_INFO};
        strcpy(set.actionSetName, "touch"); strcpy(set.localizedActionSetName, "Touch Controllers");
        XR(xrCreateActionSet(instance, &set, &actionSet));
        pose = action("aim", XR_ACTION_TYPE_POSE_INPUT);
        gripPose = action("grip_pose", XR_ACTION_TYPE_POSE_INPUT);
        trigger = action("trigger", XR_ACTION_TYPE_FLOAT_INPUT); squeeze = action("squeeze", XR_ACTION_TYPE_FLOAT_INPUT);
        stick = action("stick", XR_ACTION_TYPE_VECTOR2F_INPUT);
        primary = action("primary", XR_ACTION_TYPE_BOOLEAN_INPUT); secondary = action("secondary", XR_ACTION_TYPE_BOOLEAN_INPUT);
        click = action("click", XR_ACTION_TYPE_BOOLEAN_INPUT); menu = action("menu", XR_ACTION_TYPE_BOOLEAN_INPUT, false);
        vibration = action("vibration", XR_ACTION_TYPE_VIBRATION_OUTPUT);
        std::vector<XrActionSuggestedBinding> binds;
        for (int h = 0; h < 2; h++) {
            std::string prefix = h == 0 ? "/user/hand/left/input/" : "/user/hand/right/input/";
            auto bind = [&](XrAction a, const char* suffix) { binds.push_back({a, path((prefix+suffix).c_str())}); };
            bind(pose, "aim/pose"); bind(trigger, "trigger/value"); bind(squeeze, "squeeze/value"); bind(stick, "thumbstick");
            bind(gripPose,"grip/pose");
            bind(primary, h == 0 ? "x/click" : "a/click"); bind(secondary, h == 0 ? "y/click" : "b/click"); bind(click, "thumbstick/click");
            if (h == 0) bind(menu, "menu/click");
            binds.push_back({vibration, path((std::string(h == 0 ? "/user/hand/left" : "/user/hand/right")+"/output/haptic").c_str())});
        }
        XrInteractionProfileSuggestedBinding suggest{XR_TYPE_INTERACTION_PROFILE_SUGGESTED_BINDING};
        suggest.interactionProfile = path("/interaction_profiles/oculus/touch_controller");
        suggest.countSuggestedBindings = binds.size(); suggest.suggestedBindings = binds.data();
        XR(xrSuggestInteractionProfileBindings(instance, &suggest));
        XrSessionActionSetsAttachInfo attach{XR_TYPE_SESSION_ACTION_SETS_ATTACH_INFO}; attach.countActionSets = 1; attach.actionSets = &actionSet;
        XR(xrAttachSessionActionSets(session, &attach));
        for (int h = 0; h < 2; h++) {
            XrActionSpaceCreateInfo a{XR_TYPE_ACTION_SPACE_CREATE_INFO}; a.action = pose; a.subactionPath = paths[h]; a.poseInActionSpace.orientation.w = 1;
            XR(xrCreateActionSpace(session, &a, &hands[h]));
            a.action=gripPose;XR(xrCreateActionSpace(session,&a,&grips[h]));
        }
    }
    void events() {
        XrEventDataBuffer event{XR_TYPE_EVENT_DATA_BUFFER};
        while (xrPollEvent(instance, &event) == XR_SUCCESS) {
            if (event.type == XR_TYPE_EVENT_DATA_SESSION_STATE_CHANGED) {
                state = reinterpret_cast<XrEventDataSessionStateChanged*>(&event)->state;
                LOG("Session state %d", state);
                if (state == XR_SESSION_STATE_READY) {
                    XrSessionBeginInfo begin{XR_TYPE_SESSION_BEGIN_INFO}; begin.primaryViewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO;
                    XR(xrBeginSession(session, &begin)); running = true;
                }
                if (state == XR_SESSION_STATE_STOPPING) { running = false; XR(xrEndSession(session)); }
                if (state == XR_SESSION_STATE_EXITING || state == XR_SESSION_STATE_LOSS_PENDING) exit = true;
            } else if (event.type == XR_TYPE_EVENT_DATA_INSTANCE_LOSS_PENDING) exit = true;
            event = {XR_TYPE_EVENT_DATA_BUFFER};
        }
    }
    static std::string poseJSON(const XrSpaceLocation& loc) {
        char b[512]; auto p = loc.pose.position; auto q = loc.pose.orientation;
        if (!(loc.locationFlags & XR_SPACE_LOCATION_ORIENTATION_VALID_BIT)) q = {0,0,0,1};
        if (!(loc.locationFlags & XR_SPACE_LOCATION_POSITION_VALID_BIT)) p = {0,0,0};
        snprintf(b, sizeof(b), "\"p\":[%.6f,%.6f,%.6f],\"q\":[%.7f,%.7f,%.7f,%.7f],\"flags\":%llu", p.x,p.y,p.z,q.x,q.y,q.z,q.w,static_cast<unsigned long long>(loc.locationFlags));
        return b;
    }
    bool boolean(XrAction a, XrPath p) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO}; info.action = a; info.subactionPath = p;
        XrActionStateBoolean result{XR_TYPE_ACTION_STATE_BOOLEAN};
        XR(xrGetActionStateBoolean(session, &info, &result)); return result.isActive && result.currentState;
    }
    float scalar(XrAction a, XrPath p) {
        XrActionStateGetInfo info{XR_TYPE_ACTION_STATE_GET_INFO}; info.action = a; info.subactionPath = p;
        XrActionStateFloat result{XR_TYPE_ACTION_STATE_FLOAT}; XR(xrGetActionStateFloat(session, &info, &result)); return result.isActive ? result.currentState : 0;
    }
    std::string trackedHandJSON(int h, XrTime time, bool focused) {
        XrHandJointLocationEXT joints[XR_HAND_JOINT_COUNT_EXT]{};
        XrHandJointVelocityEXT velocities[XR_HAND_JOINT_COUNT_EXT]{};
        XrHandJointVelocitiesEXT speed{XR_TYPE_HAND_JOINT_VELOCITIES_EXT};
        speed.jointCount=XR_HAND_JOINT_COUNT_EXT; speed.jointVelocities=velocities;
        XrHandTrackingAimStateFB aim{XR_TYPE_HAND_TRACKING_AIM_STATE_FB};
        speed.next=handAimSupported ? &aim : nullptr;
        XrHandJointLocationsEXT locations{XR_TYPE_HAND_JOINT_LOCATIONS_EXT};
        locations.next=&speed; locations.jointCount=XR_HAND_JOINT_COUNT_EXT; locations.jointLocations=joints;
        XrHandJointsLocateInfoEXT info{XR_TYPE_HAND_JOINTS_LOCATE_INFO_EXT}; info.baseSpace=local; info.time=time;
        bool active=focused && handTrackingSupported && XR_SUCCEEDED(locateHandJoints(handTrackers[h],&info,&locations)) && locations.isActive;
        for (const auto& joint:joints) active=active && (joint.locationFlags & 10)==10;
        active=active && (joints[0].locationFlags & 15)==15 && !(aim.status & XR_HAND_TRACKING_AIM_SYSTEM_GESTURE_BIT_FB);
        XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION}; loc.pose.orientation.w=1;
        modelPoses[h]=loc;
        if (active) {
            loc.pose=joints[0].pose; loc.locationFlags=joints[0].locationFlags;
            modelPoses[h]=loc;
            if (aim.status & XR_HAND_TRACKING_AIM_VALID_BIT_FB) loc.pose=aim.aimPose;
        }
        handActive[h]=false; modelActive[h]=active;
        bool lv=active && (velocities[0].velocityFlags & XR_SPACE_VELOCITY_LINEAR_VALID_BIT);
        bool av=active && (velocities[0].velocityFlags & XR_SPACE_VELOCITY_ANGULAR_VALID_BIT);
        auto v=lv ? velocities[0].linearVelocity : XrVector3f{};
        auto w=av ? velocities[0].angularVelocity : XrVector3f{};
        char b[512];
        snprintf(b,sizeof(b),",\"v\":[%.6f,%.6f,%.6f],\"w\":[%.6f,%.6f,%.6f],\"lv\":%s,\"av\":%s,\"active\":%s,\"buttons\":0,\"trigger\":0,\"squeeze\":0,\"stick\":[0,0],\"joints\":[",v.x,v.y,v.z,w.x,w.y,w.z,lv?"true":"false",av?"true":"false",active?"true":"false");
        std::string result="{"+poseJSON(loc)+",\"grip\":{"+poseJSON(modelPoses[h])+"}"+b;
        if (active) for (int j=0;j<XR_HAND_JOINT_COUNT_EXT;++j) {
            auto p=joints[j].pose.position;
            snprintf(b,sizeof(b),"%s[%.6f,%.6f,%.6f]",j?",":"",p.x,p.y,p.z); result+=b;
        }
        return result+"]}";
    }
    bool transmit(XrTime time, uint64_t seq) {
        bool focused = state == XR_SESSION_STATE_FOCUSED;
        XrActiveActionSet active{actionSet, XR_NULL_PATH};
        XrActionsSyncInfo sync{XR_TYPE_ACTIONS_SYNC_INFO}; sync.countActiveActionSets = 1; sync.activeActionSets = &active;
        XrResult syncResult = xrSyncActions(session, &sync);
        focused = focused && syncResult == XR_SUCCESS;
        XrSpaceLocation hl{XR_TYPE_SPACE_LOCATION}; XR(xrLocateSpace(head, local, time, &hl));
        inputFocused = focused;
        char start[640]; snprintf(start, sizeof(start), "{\"version\":1,\"haptics\":true,\"screen_control\":true,\"screen_visible\":%s,\"screen_rendered\":%s,\"screen_updates\":%u,\"cursor_control\":true,\"cursor_visible\":%s,\"cursor_updates\":%u,\"passthrough_control\":true,\"passthrough_supported\":%s,\"passthrough_result\":%d,\"seq\":%llu,\"t\":%.9f,\"focused\":%s,\"passthrough\":%s,\"head\":{",sender.screenVisible?"true":"false",screenRendered?"true":"false",sender.screenUpdates,cursorRendered?"true":"false",sender.cursorUpdates,passthroughSupported?"true":"false",passthroughResult,static_cast<unsigned long long>(seq),time/1e9,focused?"true":"false",passthroughActive?"true":"false");
        std::string frame = std::string(start) + poseJSON(hl) + "},\"hands\":[";
        // Wire order is right (Wiimote), left (Nunchuk).
        for (int order = 0; order < 2; order++) {
            int h = 1-order;
            if (sender.handTrackingRequested) {
                if (order) frame+=",";
                frame+=trackedHandJSON(h,time,focused);
                continue;
            }
            XrSpaceVelocity vel{XR_TYPE_SPACE_VELOCITY};
            XrSpaceLocation loc{XR_TYPE_SPACE_LOCATION}; loc.next = &vel;
            XR(xrLocateSpace(hands[h], local, time, &loc));
            XR(xrLocateSpace(grips[h],local,time,&modelPoses[h]));
            XrActionStateGetInfo get{XR_TYPE_ACTION_STATE_GET_INFO}; get.action = pose; get.subactionPath = paths[h];
            XrActionStatePose statePose{XR_TYPE_ACTION_STATE_POSE}; XR(xrGetActionStatePose(session, &get, &statePose));
            bool isActive = focused && statePose.isActive;
            handActive[h] = isActive;
            get.action=gripPose;XrActionStatePose gripState{XR_TYPE_ACTION_STATE_POSE};XR(xrGetActionStatePose(session,&get,&gripState));
            modelActive[h]=focused && gripState.isActive;
            if (seq % 360 == 0) {
                XrInteractionProfileState profile{XR_TYPE_INTERACTION_PROFILE_STATE};
                xrGetCurrentInteractionProfile(session, paths[h], &profile);
                char name[256] = "none"; uint32_t size = 0;
                if (profile.interactionProfile) xrPathToString(instance, profile.interactionProfile, sizeof(name), &size, name);
                LOG("Hand %s profile=%s pose_active=%u flags=%llu focused=%d sync=%d", h?"right":"left", name, statePose.isActive, static_cast<unsigned long long>(loc.locationFlags), focused, syncResult);
            }
            XrActionStateVector2f xy{XR_TYPE_ACTION_STATE_VECTOR2F}; get.action = stick; XR(xrGetActionStateVector2f(session, &get, &xy));
            uint32_t buttons = 0; float tr = 0, sq = 0;
            if (isActive) {
                buttons = (boolean(primary, paths[h])?1:0) | (boolean(secondary, paths[h])?2:0) | (boolean(click, paths[h])?4:0) | (h == 0 && boolean(menu, XR_NULL_PATH)?8:0);
                tr = scalar(trigger, paths[h]); sq = scalar(squeeze, paths[h]);
            }
            bool lv = vel.velocityFlags & XR_SPACE_VELOCITY_LINEAR_VALID_BIT, av = vel.velocityFlags & XR_SPACE_VELOCITY_ANGULAR_VALID_BIT;
            auto v = lv ? vel.linearVelocity : XrVector3f{0,0,0};
            auto w = av ? vel.angularVelocity : XrVector3f{0,0,0};
            char b[768];
            snprintf(b, sizeof(b), ",\"v\":[%.6f,%.6f,%.6f],\"w\":[%.6f,%.6f,%.6f],\"lv\":%s,\"av\":%s,\"active\":%s,\"buttons\":%u,\"trigger\":%.5f,\"squeeze\":%.5f,\"stick\":[%.5f,%.5f]}", v.x,v.y,v.z,w.x,w.y,w.z,lv?"true":"false",av?"true":"false",isActive?"true":"false", buttons,tr,sq,isActive&&xy.isActive?xy.currentState.x:0,isActive&&xy.isActive?xy.currentState.y:0);
            frame += (order ? ",{": "{") + poseJSON(loc) + ",\"grip\":{"+poseJSON(modelPoses[h])+"}" + b;
        }
        frame += "],\"hand_tracking_control\":true,\"hand_tracking_supported\":"+std::string(handTrackingSupported?"true":"false")+",\"hand_tracking\":"+(sender.handTrackingRequested?"true":"false")+",\"controller_models\":true,\"model_gl_error\":"+std::to_string(modelGLError)+",\"model_frames\":"+std::to_string(modelFrames)+",\"token\":\"" + token + "\"}\n";
        return sender.sendFrame(frame.c_str(), frame.size());
    }
    void haptics(bool connected) {
        sender.receiveFeedback();
        double now = Sender::seconds();
        for (int h = 0; h < 2; ++h) {
            float amplitude = connected && sender.fd >= 0 && inputFocused && handActive[h] ? sender.amplitudes[1-h] : 0;
            bool changed = amplitude != lastAmplitude[h];
            if (!changed && (amplitude == 0 || now-lastHaptic[h] < .1)) continue;
            XrHapticActionInfo info{XR_TYPE_HAPTIC_ACTION_INFO}; info.action = vibration; info.subactionPath = paths[h];
            XrResult result;
            if (amplitude > 0) {
                XrHapticVibration pulse{XR_TYPE_HAPTIC_VIBRATION};
                pulse.amplitude = amplitude; pulse.frequency = XR_FREQUENCY_UNSPECIFIED; pulse.duration = 200000000;
                result = xrApplyHapticFeedback(session, &info, reinterpret_cast<XrHapticBaseHeader*>(&pulse));
            } else result = xrStopHapticFeedback(session, &info);
            if (changed) LOG("Haptic %s amplitude=%.3f result=%d", h ? "right" : "left", amplitude, result);
            lastAmplitude[h] = amplitude; lastHaptic[h] = now;
        }
    }
    void frame(uint64_t seq) {
        XrFrameWaitInfo wait{XR_TYPE_FRAME_WAIT_INFO}; XrFrameState fs{XR_TYPE_FRAME_STATE};
        XR(xrWaitFrame(session, &wait, &fs));
        XrFrameBeginInfo begin{XR_TYPE_FRAME_BEGIN_INFO}; XR(xrBeginFrame(session, &begin));
        bool connected = transmit(fs.predictedDisplayTime, seq);
        haptics(connected);
        if (sender.passthroughRequested != passthroughActive && passthroughLayer && Sender::seconds()-lastPassthroughAttempt > 1) {
            lastPassthroughAttempt = Sender::seconds();
            if (sender.passthroughRequested) {
                passthroughResult = startPassthrough(passthrough);
                if (XR_SUCCEEDED(passthroughResult)) passthroughResult = resumePassthroughLayer(passthroughLayer);
                passthroughActive = XR_SUCCEEDED(passthroughResult);
            } else {
                passthroughResult = pausePassthroughLayer(passthroughLayer);
                if (XR_SUCCEEDED(passthroughResult)) passthroughResult = pausePassthrough(passthrough);
                if (XR_SUCCEEDED(passthroughResult)) passthroughActive = false;
            }
            LOG("Passthrough active=%d requested=%d result=%d",passthroughActive,sender.passthroughRequested,passthroughResult);
        }
        XrViewLocateInfo locate{XR_TYPE_VIEW_LOCATE_INFO}; locate.viewConfigurationType = XR_VIEW_CONFIGURATION_TYPE_PRIMARY_STEREO; locate.displayTime = fs.predictedDisplayTime; locate.space = local;
        XrViewState vs{XR_TYPE_VIEW_STATE}; XrView views[2]{{XR_TYPE_VIEW}, {XR_TYPE_VIEW}}; uint32_t count = 0;
        XR(xrLocateViews(session, &locate, &vs, 2, &count, views));
        XrCompositionLayerProjectionView projectionViews[2]{{XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}, {XR_TYPE_COMPOSITION_LAYER_PROJECTION_VIEW}};
        std::vector<const XrCompositionLayerBaseHeader*> layers;
        XrCompositionLayerPassthroughFB pass{XR_TYPE_COMPOSITION_LAYER_PASSTHROUGH_FB}; pass.layerHandle = passthroughLayer;
        pass.flags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
        if (fs.shouldRender && passthroughActive) layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader*>(&pass));
        bool render = fs.shouldRender && (vs.viewStateFlags & XR_VIEW_STATE_ORIENTATION_VALID_BIT);
        if (render) for (int i = 0; i < 2; i++) {
            auto& eye = eyes[i]; uint32_t image = 0;
            XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO}; XR(xrAcquireSwapchainImage(eye.swapchain, &acquire, &image));
            XrSwapchainImageWaitInfo wi{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO}; wi.timeout = XR_INFINITE_DURATION; XR(xrWaitSwapchainImage(eye.swapchain, &wi));
            glBindFramebuffer(GL_FRAMEBUFFER, fbo);
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, eye.images[image].image, 0);
            glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,eye.depth);
            glViewport(0,0,eye.width,eye.height);
            glDisable(GL_SCISSOR_TEST);
            glDepthMask(GL_TRUE);glClearColor(0.f,0.f,0.f,0.f); glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
            controllerModels.draw(views[i],modelPoses,modelActive);
            glFlush();
            XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO}; XR(xrReleaseSwapchainImage(eye.swapchain, &release));
            projectionViews[i].pose = views[i].pose; projectionViews[i].fov = views[i].fov;
            projectionViews[i].subImage.swapchain = eye.swapchain; projectionViews[i].subImage.imageRect.extent = {eye.width,eye.height};
        }
        if (render) { ++modelFrames;GLenum error=glGetError();if (error!=GL_NO_ERROR) modelGLError=error; }
        XrCompositionLayerProjection projection{XR_TYPE_COMPOSITION_LAYER_PROJECTION};
        projection.space = local; projection.viewCount = 2; projection.views = projectionViews;
        projection.layerFlags = XR_COMPOSITION_LAYER_BLEND_TEXTURE_SOURCE_ALPHA_BIT;
        XrCompositionLayerQuad panels[2]{{XR_TYPE_COMPOSITION_LAYER_QUAD},{XR_TYPE_COMPOSITION_LAYER_QUAD}};
        screenRendered = render && sender.screenVisible;
        cursorRendered = screenRendered && sender.cursorVisible && Sender::seconds()-sender.lastCursor<.3;
        if (screenRendered) {
            for (int face=0;face<2;++face) {
                auto& screenImage=screenImages[face];
                uint32_t image = 0;
                XrSwapchainImageAcquireInfo acquire{XR_TYPE_SWAPCHAIN_IMAGE_ACQUIRE_INFO}; XR(xrAcquireSwapchainImage(screenImage.swapchain,&acquire,&image));
                XrSwapchainImageWaitInfo wi{XR_TYPE_SWAPCHAIN_IMAGE_WAIT_INFO}; wi.timeout=XR_INFINITE_DURATION; XR(xrWaitSwapchainImage(screenImage.swapchain,&wi));
                glBindFramebuffer(GL_FRAMEBUFFER,fbo);
                glFramebufferTexture2D(GL_FRAMEBUFFER,GL_COLOR_ATTACHMENT0,GL_TEXTURE_2D,screenImage.images[image].image,0);
                glFramebufferRenderbuffer(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,GL_RENDERBUFFER,0);
                glViewport(0,0,screenImage.width,screenImage.height);
                glDisable(GL_SCISSOR_TEST); glClearColor(.06f,.10f,.16f,1); glClear(GL_COLOR_BUFFER_BIT);
                glEnable(GL_SCISSOR_TEST);
                auto line = [](int x,int y,int w,int h) { glScissor(x,y,w,h); glClear(GL_COLOR_BUFFER_BIT); };
                glClearColor(.48f,.65f,.73f,1);
                line(0,0,512,2);line(0,510,512,2);line(0,0,2,512);line(510,0,2,512);
                glClearColor(.28f,.38f,.44f,1);
                line(255,0,2,512);line(0,255,512,2);
                float width=std::min(sender.screenSize.x,sender.screenSize.y*sender.screenAspect),height=width/sender.screenAspect;
                int w=std::max(2,int(512*width/sender.screenSize.x)),h=std::max(2,int(512*height/sender.screenSize.y));
                int x=(512-w)/2,y=(512-h)/2;
                glClearColor(.35f,.81f,.68f,1);
                line(x,y,w,2);line(x,y+h-2,w,2);line(x,y,2,h);line(x+w-2,y,2,h);
                if (cursorRendered) {
                    float cx=512*(face ? 1-sender.cursorU : sender.cursorU),cy=512*(1-sender.cursorV);
                    float angle=sender.cursorRoll*3.14159265f,c=cosf(angle),s=sinf(angle);
                    glClearColor(.514f,.949f,.788f,1);
                    for (int arm=0;arm<2;++arm) {
                        float dx=.025f*512*(arm ? -s : c)/sender.screenSize.x*(face ? -1 : 1);
                        float dy=.025f*512*(arm ? c : s)/sender.screenSize.y;
                        int steps=std::max(1,int(ceilf(2*std::max(fabsf(dx),fabsf(dy)))));
                        // ponytail: Reuse scissor drawing for two short arms; use a line shader if profiling warrants it.
                        for (int n=0;n<=steps;++n) {
                            float t=2.f*n/steps-1;
                            line(int(lroundf(cx+t*dx))-1,int(lroundf(cy+t*dy))-1,2,2);
                        }
                    }
                }
                glDisable(GL_SCISSOR_TEST);glFlush();
                XrSwapchainImageReleaseInfo release{XR_TYPE_SWAPCHAIN_IMAGE_RELEASE_INFO}; XR(xrReleaseSwapchainImage(screenImage.swapchain,&release));
            }
            for (int face=0;face<2;++face) {
                auto& panel=panels[face];auto& screenImage=screenImages[face];
                panel.space=local;panel.eyeVisibility=XR_EYE_VISIBILITY_BOTH;
                panel.subImage.swapchain=screenImage.swapchain;panel.subImage.imageRect.extent={screenImage.width,screenImage.height};
                panel.pose=sender.screenPose;panel.size={sender.screenSize.x,sender.screenSize.y};
            }
            // OpenXR quads have one visible face; the desktop panel is visible from either side.
            auto q=sender.screenPose.orientation;panels[1].pose.orientation={-q.z,q.w,q.x,-q.y};
            for (auto& panel:panels) layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader*>(&panel));
        }
        if (render) layers.push_back(reinterpret_cast<XrCompositionLayerBaseHeader*>(&projection));
        XrFrameEndInfo end{XR_TYPE_FRAME_END_INFO}; end.displayTime = fs.predictedDisplayTime; end.environmentBlendMode = XR_ENVIRONMENT_BLEND_MODE_OPAQUE;
        end.layerCount = layers.size(); end.layers = layers.data(); XR(xrEndFrame(session, &end));
    }
    ~Bridge() {
        controllerModels.destroy();
        for (auto tracker:handTrackers) if (tracker && destroyHandTracker) destroyHandTracker(tracker);
        if (passthroughLayer && destroyPassthroughLayer) destroyPassthroughLayer(passthroughLayer);
        if (passthrough && destroyPassthrough) destroyPassthrough(passthrough);
        for (auto& e: eyes) { if (e.depth) glDeleteRenderbuffers(1,&e.depth);if (e.swapchain) xrDestroySwapchain(e.swapchain); }
        for (auto& e:screenImages) if (e.swapchain) xrDestroySwapchain(e.swapchain);
        for (auto s: hands) if (s) xrDestroySpace(s);
        for (auto s: grips) if (s) xrDestroySpace(s);
        if (head) xrDestroySpace(head); if (local) xrDestroySpace(local);
        if (session) xrDestroySession(session); if (actionSet) xrDestroyActionSet(actionSet); if (instance) xrDestroyInstance(instance);
        if (display != EGL_NO_DISPLAY) {
            eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
            if (context != EGL_NO_CONTEXT) eglDestroyContext(display,context);
            if (surface != EGL_NO_SURFACE) eglDestroySurface(display,surface);
            eglTerminate(display);
        }
        app->activity->vm->DetachCurrentThread();
    }
};

void android_main(android_app* app) {
    app->onInputEvent = [](android_app*, AInputEvent* event) -> int32_t {
        if (AInputEvent_getType(event) != AINPUT_EVENT_TYPE_KEY) return 0;
        const int key = AKeyEvent_getKeyCode(event);
        return key == AKEYCODE_BACK || key == AKEYCODE_BUTTON_B;
    };
    ANativeActivity_setWindowFlags(app->activity, AWINDOW_FLAG_KEEP_SCREEN_ON, 0);
    try {
        Bridge bridge(app); bridge.init(); uint64_t seq = 0;
        while (!app->destroyRequested && !bridge.exit) {
            int events = 0; android_poll_source* source = nullptr;
            while (ALooper_pollOnce(bridge.running ? 0 : 100, nullptr, &events, reinterpret_cast<void**>(&source)) >= 0) {
                if (source) source->process(app, source);
                if (app->destroyRequested) break;
                if (!bridge.running) break;
            }
            bridge.events();
            if (bridge.running) bridge.frame(seq++);
        }
    } catch (const std::exception& e) { LOG("Fatal: %s", e.what()); }
    ANativeActivity_finish(app->activity);
}
