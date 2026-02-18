import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Button

# -------------------------
# Helpers
# -------------------------
def norm(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v

def set_equal_3d(ax, pts, zoom=1.0):
    pts = np.asarray(pts)
    mins = pts.min(axis=0)
    maxs = pts.max(axis=0)
    span = (maxs - mins).max()
    mid = (maxs + mins) / 2.0
    span = span / float(zoom)
    ax.set_xlim(mid[0] - span/2, mid[0] + span/2)
    ax.set_ylim(mid[1] - span/2, mid[1] + span/2)
    ax.set_zlim(mid[2] - span/2, mid[2] + span/2)

def ray_to_image_plane(C, r, z_plane):
    # Ray: X(t)=C+t r, intersect z=z_plane
    if abs(r[2]) < 1e-12:
        return None, None, None
    t = (z_plane - C[2]) / r[2]
    P = C + t * r
    return P[0], P[1], t

def ray_plane_intersection(C, r, n, d, eps=1e-9):
    # Plane: n·X + d = 0
    denom = float(np.dot(n, r))
    if abs(denom) < eps:
        return None, None
    t = -(np.dot(n, C) + d) / denom
    X = C + t * r
    return t, X

# Radial + tangential distortion (OpenCV form) on normalized image coords (x,y)
def distort_xy(x, y, k1=0.15, k2=0.05, k3=0.0, p1=0.0, p2=0.0):
    r2 = x*x + y*y
    r4 = r2*r2
    r6 = r4*r2
    radial = 1.0 + k1*r2 + k2*r4 + k3*r6
    x_tan = 2.0*p1*x*y + p2*(r2 + 2.0*x*x)
    y_tan = p1*(r2 + 2.0*y*y) + 2.0*p2*x*y
    xd = x*radial + x_tan
    yd = y*radial + y_tan
    return xd, yd

def build_laser_fan_surface(L, sheet_u, sheet_v, t_max=3.0, s_min=-1.5, s_max=1.5, Ns=60, Nt=35):
    s_vals = np.linspace(s_min, s_max, Ns)
    t_vals = np.linspace(0.0, t_max, Nt)
    dirs = np.array([norm(sheet_v + s*sheet_u) for s in s_vals])
    Xsurf = np.zeros((Nt, Ns, 3))
    for j, t in enumerate(t_vals):
        Xsurf[j, :, :] = L + t*dirs
    return Xsurf

# -------------------------
# Scene parameters
# -------------------------
C = np.array([0.0, 0.0, 0.0])   # camera centre

# Image plane at z = -f (forward = -Z)
f = 0.5                         # << changed as requested

# Laser source point
L = np.array([0.6, 0.0, -0.2])

# Laser plane basis (laser sheet lives in span{sheet_u, sheet_v} through L)
sheet_u = norm(np.array([0.0, 1.0, 0.0]))     # sweep direction
sheet_v = norm(np.array([0.0, 0.0, -1.0]))    # down direction

# Laser plane equation n·X + d = 0
n = norm(np.cross(sheet_u, sheet_v))
d = -np.dot(n, L)

# Distortion parameters (exaggerated slightly so you can SEE it)
k1, k2, k3 = 0.45, 0.20, 0.0
p1, p2     = 0.02, -0.02

# Surface sampling on the laser plane
N = 25
U_SPAN = 2.2
u_vals = np.linspace(-U_SPAN/2, U_SPAN/2, N)

# Sine profile parameters
v0 = 1.3
A = 0.35
k = 2*np.pi / 2.0  # ~one period across ~2 units of u

def make_surface_points(profile_name: str):
    if profile_name == "flat":
        v_vals = v0 + 0.0*u_vals
    elif profile_name == "sine":
        v_vals = v0 + A*np.sin(k*u_vals)
    else:
        raise ValueError("profile_name must be 'flat' or 'sine'")

    # True surface hits ON the laser plane:
    # X(u) = L + u*sheet_u + v(u)*sheet_v
    X_true = L[None, :] + u_vals[:, None]*sheet_u[None, :] + v_vals[:, None]*sheet_v[None, :]
    return X_true

def project_and_reconstruct(X_true):
    uv_ideal = []
    uv_dist  = []
    X_wrong  = []

    for Xi in X_true:
        # True ray is exactly C->Xi
        r_true = norm(Xi - C)

        # Ideal pixel on image plane z=-f
        u_i, v_i, _ = ray_to_image_plane(C, r_true, z_plane=-f)
        uv_ideal.append([u_i, v_i])

        # Distort the pixel
        u_d, v_d = distort_xy(u_i, v_i, k1=k1, k2=k2, k3=k3, p1=p1, p2=p2)
        uv_dist.append([u_d, v_d])

        # Ignore distortion: build ray from distorted pixel as if ideal
        r_wrong = norm(np.array([u_d, v_d, -f]))

        # Intersect wrong ray with the laser plane -> "camera believes surface is here"
        t_hit, Xh = ray_plane_intersection(C, r_wrong, n, d)
        if t_hit is None or t_hit < 0:
            X_wrong.append([np.nan, np.nan, np.nan])
        else:
            X_wrong.append(Xh)

    return np.array(uv_ideal), np.array(uv_dist), np.array(X_wrong)

# -------------------------
# Plot setup
# -------------------------
fig = plt.figure(figsize=(11, 7))
ax = fig.add_subplot(111, projection="3d")
plt.subplots_adjust(bottom=0.18)

# Keep buttons alive (references)
btn_axes = []
buttons = []

# View presets
VIEWS = {
    "Iso":        (25, -55),
    "Top (XY)":   (90, -90),
    "Front (XZ)": (0, -90),
    "Right (YZ)": (0, 0),
}

current_view = {"name": "Iso"}

def style_axes():
    ax.set_title("Laser triangulation: distortion shifts pixels → wrong rays → biased 3D (flat/sine toggle)")
    ax.grid(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_zticks([])
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        try:
            axis.line.set_alpha(0.35)
        except Exception:
            pass
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")

def apply_view(name):
    current_view["name"] = name
    elev, azim = VIEWS[name]
    ax.view_init(elev=elev, azim=azim)
    fig.canvas.draw_idle()

def draw_scene(profile_name: str):
    # Preserve current view angles
    view_name = current_view["name"]

    ax.cla()
    style_axes()

    # Laser fan surface (visual only)
    Xsurf = build_laser_fan_surface(L, sheet_u, sheet_v, t_max=3.2)
    ax.plot_surface(Xsurf[:, :, 0], Xsurf[:, :, 1], Xsurf[:, :, 2], alpha=0.18, linewidth=0)

    # Build surface and recon
    X_true = make_surface_points(profile_name)
    uv_ideal, uv_dist, X_wrong = project_and_reconstruct(X_true)

    # Image plane z=-f
    ip = np.array([
        [-0.7, -0.5, -f],
        [ 0.7, -0.5, -f],
        [ 0.7,  0.5, -f],
        [-0.7,  0.5, -f],
        [-0.7, -0.5, -f],
    ])
    ax.plot(ip[:, 0], ip[:, 1], ip[:, 2], alpha=0.6, label="image plane z=-f")

    # Camera + laser points
    ax.scatter([C[0]], [C[1]], [C[2]], s=40, label="camera C")
    ax.scatter([L[0]], [L[1]], [L[2]], s=40, label="laser source L")

    # True hits (laser plane)
    ax.scatter(X_true[:, 0], X_true[:, 1], X_true[:, 2], s=28, label=f"true hits (laser plane, {profile_name})")

    # Wrong hits (ignore distortion)
    mask_ok = np.isfinite(X_wrong[:, 0])
    ax.scatter(X_wrong[mask_ok, 0], X_wrong[mask_ok, 1], X_wrong[mask_ok, 2],
               s=28, label="reconstructed hits (ignore distortion)")

    # Rays (light)
    for Xi, Xw in zip(X_true, X_wrong):
        seg = np.vstack([C, Xi])
        ax.plot(seg[:, 0], seg[:, 1], seg[:, 2], alpha=0.12)
        if np.all(np.isfinite(Xw)):
            seg2 = np.vstack([C, Xw])
            ax.plot(seg2[:, 0], seg2[:, 1], seg2[:, 2], alpha=0.12)

    # Pixels on image plane (ideal vs distorted)
    ax.scatter(uv_ideal[:, 0], uv_ideal[:, 1], np.full(N, -f), s=18, label="pixels (ideal/undistorted)")
    ax.scatter(uv_dist[:, 0],  uv_dist[:, 1],  np.full(N, -f), s=18, label="pixels (distorted)")

    # Limits + zoom
    pts_for_limits = np.vstack([
        np.array([C, L]),
        X_true,
        X_wrong[mask_ok],
        np.array([[-0.7, -0.5, -f], [0.7, 0.5, -f]]),
        Xsurf.reshape(-1, 3),
    ])
    set_equal_3d(ax, pts_for_limits, zoom=2.0)

    ax.legend(loc="upper left", framealpha=0.9)

    # Restore the chosen view
    apply_view(view_name)

    # Print a quick error metric
    err = np.nanmean(np.linalg.norm(X_wrong - X_true, axis=1))
    print(f"[{profile_name}] mean 3D error if ignore distortion = {err:.4f}")

# -------------------------
# Buttons
# -------------------------
def make_button(x, y, w, h, label, callback):
    bax = fig.add_axes([x, y, w, h])
    b = Button(bax, label)
    b.on_clicked(callback)
    btn_axes.append(bax)
    buttons.append(b)
    return b

# Profile toggle buttons (left)
make_button(0.02, 0.04, 0.10, 0.06, "Flat", lambda _e: draw_scene("flat"))
make_button(0.13, 0.04, 0.10, 0.06, "Sine", lambda _e: draw_scene("sine"))

# View buttons (right)
vx0 = 0.30
vw, vh, vpad = 0.12, 0.06, 0.01
for i, name in enumerate(VIEWS.keys()):
    make_button(vx0 + i*(vw+vpad), 0.04, vw, vh, name, lambda _e, n=name: apply_view(n))

# Initial draw
draw_scene("sine")

plt.show()

print("Distortion params:", dict(k1=k1, k2=k2, k3=k3, p1=p1, p2=p2))
print("f =", f)
