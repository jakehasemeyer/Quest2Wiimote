#include <android/asset_manager.h>

struct ControllerModels {
    struct Mesh { GLuint vao{},buffer{},texture{}; GLsizei count{}; } meshes[2]; // Right, left.
    GLuint program{};
    GLint projection{},eyePosition{},eyeRotation{},position{},rotation{};

    static GLuint shader(GLenum kind,const char* source) {
        GLuint value=glCreateShader(kind);glShaderSource(value,1,&source,nullptr);glCompileShader(value);
        GLint okay=0;glGetShaderiv(value,GL_COMPILE_STATUS,&okay);
        if (!okay) { char log[1024];glGetShaderInfoLog(value,sizeof(log),nullptr,log);LOG("Controller shader: %s",log);glDeleteShader(value);throw std::runtime_error("Controller shader compilation failed"); }
        return value;
    }
    void init(AAssetManager* assets) {
        const char* vertex=R"(#version 300 es
layout(location=0) in vec3 position;
layout(location=1) in vec3 normal;
layout(location=2) in vec2 uv;
uniform mat4 projection;
uniform vec3 eyePosition, controllerPosition;
uniform vec4 eyeRotation, controllerRotation;
out vec3 n;out vec2 texcoord;
vec3 rotate(vec4 q,vec3 v){return v+2.0*cross(q.xyz,cross(q.xyz,v)+q.w*v);}
void main(){
    vec3 world=rotate(controllerRotation,position)+controllerPosition;
    gl_Position=projection*vec4(rotate(eyeRotation,world-eyePosition),1);
    n=rotate(controllerRotation,normal);texcoord=uv;
})";
        const char* fragment=R"(#version 300 es
precision mediump float;
in vec3 n;in vec2 texcoord;
uniform sampler2D image;
out vec4 color;
void main(){
    float light=.6+.4*max(dot(normalize(n),normalize(vec3(-.4,.8,.5))),0.0);
    color=vec4(texture(image,texcoord).rgb*light,1);
})";
        GLuint vs=shader(GL_VERTEX_SHADER,vertex),fs=shader(GL_FRAGMENT_SHADER,fragment);
        program=glCreateProgram();glAttachShader(program,vs);glAttachShader(program,fs);glLinkProgram(program);
        glDeleteShader(vs);glDeleteShader(fs);
        GLint okay=0;glGetProgramiv(program,GL_LINK_STATUS,&okay);
        if (!okay) { char log[1024];glGetProgramInfoLog(program,sizeof(log),nullptr,log);LOG("Controller program: %s",log);throw std::runtime_error("Controller shader linking failed"); }
        projection=glGetUniformLocation(program,"projection");eyePosition=glGetUniformLocation(program,"eyePosition");eyeRotation=glGetUniformLocation(program,"eyeRotation");
        position=glGetUniformLocation(program,"controllerPosition");rotation=glGetUniformLocation(program,"controllerRotation");
        glUseProgram(program);glUniform1i(glGetUniformLocation(program,"image"),0);
        const char* names[]{"right.q2cm","left.q2cm"};
        for (int h=0;h<2;++h) {
            AAsset* asset=AAssetManager_open(assets,names[h],AASSET_MODE_BUFFER);
            if (!asset) throw std::runtime_error("Controller model asset missing");
            const char* data=static_cast<const char*>(AAsset_getBuffer(asset));size_t size=AAsset_getLength(asset);
            uint32_t header[4]{};if (data && size>=16) memcpy(header,data,16);
            size_t vertexBytes=size_t(header[1])*32;
            if (!data || size<16 || memcmp(data,"Q2CM",4) || size!=16+vertexBytes+size_t(header[2])*header[3]*4) {
                AAsset_close(asset);throw std::runtime_error("Invalid controller model asset");
            }
            auto& mesh=meshes[h];mesh.count=header[1];
            glGenVertexArrays(1,&mesh.vao);glBindVertexArray(mesh.vao);
            glGenBuffers(1,&mesh.buffer);glBindBuffer(GL_ARRAY_BUFFER,mesh.buffer);glBufferData(GL_ARRAY_BUFFER,vertexBytes,data+16,GL_STATIC_DRAW);
            for (int a=0;a<3;++a) {
                glEnableVertexAttribArray(a);glVertexAttribPointer(a,a==2?2:3,GL_FLOAT,GL_FALSE,32,reinterpret_cast<void*>(a*12));
            }
            glGenTextures(1,&mesh.texture);glBindTexture(GL_TEXTURE_2D,mesh.texture);
            glTexImage2D(GL_TEXTURE_2D,0,GL_SRGB8_ALPHA8,header[2],header[3],0,GL_RGBA,GL_UNSIGNED_BYTE,data+16+vertexBytes);
            glGenerateMipmap(GL_TEXTURE_2D);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR_MIPMAP_LINEAR);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR);
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
            LOG("Controller mesh %s vertices=%u texture=%ux%u GL=%u",names[h],header[1],header[2],header[3],glGetError());
            AAsset_close(asset);
        }
        glBindVertexArray(0);glBindBuffer(GL_ARRAY_BUFFER,0);glBindTexture(GL_TEXTURE_2D,0);glUseProgram(0);
    }
    void draw(const XrView& eye,const XrSpaceLocation poses[2],const bool active[2]) {
        float l=tanf(eye.fov.angleLeft),r=tanf(eye.fov.angleRight),b=tanf(eye.fov.angleDown),t=tanf(eye.fov.angleUp);
        const float near=.025f,far=100.f;
        float matrix[]{2/(r-l),0,0,0, 0,2/(t-b),0,0, (r+l)/(r-l),(t+b)/(t-b),-(far+near)/(far-near),-1, 0,0,-2*far*near/(far-near),0};
        glEnable(GL_DEPTH_TEST);glDepthMask(GL_TRUE);glDepthFunc(GL_LEQUAL);glDisable(GL_CULL_FACE);glDisable(GL_BLEND);
        glUseProgram(program);glUniformMatrix4fv(projection,1,GL_FALSE,matrix);
        auto p=eye.pose.position;auto q=eye.pose.orientation;
        glUniform3f(eyePosition,p.x,p.y,p.z);glUniform4f(eyeRotation,-q.x,-q.y,-q.z,q.w);
        glActiveTexture(GL_TEXTURE0);
        for (int h=0;h<2;++h) {
            if (!active[h] || (poses[h].locationFlags&15)!=15) continue;
            const auto& mesh=meshes[1-h];p=poses[h].pose.position;q=poses[h].pose.orientation;
            glUniform3f(position,p.x,p.y,p.z);glUniform4f(rotation,q.x,q.y,q.z,q.w);
            glBindVertexArray(mesh.vao);glBindTexture(GL_TEXTURE_2D,mesh.texture);glDrawArrays(GL_TRIANGLES,0,mesh.count);
        }
        glBindVertexArray(0);glBindTexture(GL_TEXTURE_2D,0);glUseProgram(0);glDisable(GL_DEPTH_TEST);
    }
    void destroy() {
        for (auto& mesh:meshes) { glDeleteVertexArrays(1,&mesh.vao);glDeleteBuffers(1,&mesh.buffer);glDeleteTextures(1,&mesh.texture); }
        if (program) glDeleteProgram(program);
    }
};
