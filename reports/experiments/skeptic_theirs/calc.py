import numpy as np
from scipy.optimize import brentq
c=343.0
print("== check their worked example: f0=130, vr=62.7 ->", 130*c/(c+62.7))
# geometry implied by example: v=90, 5 s after CPA, vr=62.7
v=90; t=5; vr=62.7; r=v*v*t/vr; d=np.sqrt(r**2-(v*t)**2); print("implied r,d",r,d)
print("delay 12 s -> slant", 12*c)

def curves(f0,v,d,c=343.0,T=90,dt=0.05):
    te=np.arange(-T,T,dt)
    r=np.sqrt(d*d+(v*te)**2); vr=v*v*te/r
    f=f0*c/(c+vr)
    tr=te+r/c  # correct: received time
    naive_t=te; naive_f=f  # naive: radial velocity at time of reception
    return tr,f,naive_t,naive_f
for (f0,v,d) in [(130,90,463),(130,100,1000),(130,60,300),(130,100,3000)]:
    tr,f,nt,nf=curves(f0,v,d)
    grid=np.arange(-60,60,0.05)
    fc=np.interp(grid,tr,f)
    best=(1e9,0)
    for sh in np.arange(-15,15,0.01):
        fn=np.interp(grid,nt+sh,nf)
        rms=np.sqrt(np.mean((fn-fc)**2)); mx=np.max(np.abs(fn-fc))
        if rms<best[0]: best=(rms,sh,mx)
    maxroc=f0*v*v/(c*d)
    print(f"f0={f0} v={v} d={d}: naive vs correct best shift {best[1]:.2f}s, rms {best[0]:.2f} Hz, max {best[2]:.2f} Hz; maxRoC {maxroc:.2f} Hz/s; d/c {d/c:.2f}s")

print("== speed of sound vs temperature")
for T in [10,20,25,30]:
    cc=331.3+0.606*T; print(T, round(cc,1), f"{(cc-343)/343*100:+.2f}%")
# frequency sensitivity to c at vr=+-100
for vr in [60,100]:
    print("dlnf/dlnc at vr",vr, vr/c/(1+vr/c))
# v inversion sensitivity: v = c (fp-fq)/(fp+fq) -> v scales with c
print("v error for 1% c at v=100:",1.0,"m/s; d scales ~c too")

print("== height error -> horizontal distance error")
for h,s in [(300,600),(500,1000),(600,800),(1000,1500),(600,650)]:
    dh=np.sqrt(s*s-h*h)
    for err in [32,85,140]:
        h2=h+err
        dh2=np.sqrt(max(s*s-h2*h2,0))
        print(f"h={h} slant={s} horiz={dh:.0f}  height err {err} m -> horiz {dh2:.0f} (shift {dh2-dh:+.0f} m)")
print("pressure altitude: ~8.3 m/hPa at low levels; +-10 hPa ->", 83)

print("== Lloyd's mirror notches for mic height h_m, elevation angle th")
for hm in [1.5,5,8]:
    for th in [15,30,60,90]:
        D=2*hm*np.sin(np.radians(th))
        notches=[(2*n+1)*c/(2*D) for n in range(4)]
        print(f"hm={hm} th={th}: path diff {D:.2f} m, notches(Hz) {[round(x) for x in notches if x<600]}")

print("== along-track ambiguity v*dt")
for v in [50,70,100]:
    print(v,[v*dt for dt in [0.5,1,2,5,30]])
print("== 2-decimal coordinate precision at lat 40.87")
print("0.01 deg lat =",0.01*111.0,"km; 0.01 deg lon =",0.01*111.32*np.cos(np.radians(40.87)),"km")
print("half-diagonal 200m",100*np.sqrt(2),"100m",50*np.sqrt(2))
print("== FFT chirp smear: at maxRoC R Hz/s, window T s smears R*T Hz; bin=1/T")
for R in [1,4,10]:
    Topt=np.sqrt(1/R)
    print(f"R={R} Hz/s: window at which smear=bin is {Topt:.2f}s (bin {1/Topt:.2f} Hz)")
print("== audibility: spherical spreading 300m -> 10km:",20*np.log10(10000/300),"dB")
