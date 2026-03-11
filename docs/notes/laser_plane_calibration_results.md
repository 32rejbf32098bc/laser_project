first laser claib:

pi@Pi:~/laser_project $ python3 -m src.working_standalone_scripts.calib_laser_plane --cam-yaml calib/camera.yaml --images data/raw/lasercalib --out-yaml calib/laser_plane.yaml --rows 10 --cols 7 --square-mm 15.5 Used images : 22/35 Total 3D points : 19,932 (mm) Laser plane (cam): n=[-0.0614587744167323, 0.528487636397343, 0.8467134327624327], d=-206.686006 mm Residual |dist| : mean=12.3732 mm median=11.0551 mm P95=31.0558 mm max=47.1559 mm Wrote: calib/laser_plane.yaml

second:

pi@Pi:~/laser_project $ python3 -m src.working_standalone_scripts.calib_laser_plane --cam-yaml calib/camera.yaml --images data/raw/lasercalib4 --out-yaml calib/laser_plane4.yaml --r ows 10 --cols 7 --square-mm 15.5 Used images : 20/40 Total 3D points : 100,000 (mm) Laser plane (cam): n=[-0.09192272667967781, 0.9839542384541896, 0.15291915820396468], d=-49.745309 mm Residual |dist| : mean=23.4026 mm median=19.6978 mm P95=55.9870 mm max=115.7099 mm Wrote: calib/laser_plane4.yaml


third:

pi@Pi:~/laser_project $ python3 -m src.working_standalone_scripts.calib_laser_plane \ --cam-yaml calib/camera.yaml \ --images data/raw/lasercalib4 \ --out-yaml calib/laser_plane4.yaml \ --rows 10 --cols 7 --square-mm 15.5 \ --debug --debug-dir calib/debug_laserplane4 \ --robust --inlier-mm 2.0 Used images : 16/40 Total 3D points : 13,438 (mm) Laser plane (cam): n=[0.9700455037726962, -0.02379287448666936, 0.24175528894325662], d=-83.019066 mm Residual |dist| : mean=0.1395 mm median=0.1271 mm P95=0.3165 mm max=0.8311 mm\