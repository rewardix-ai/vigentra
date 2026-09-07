import warnings, json, sys; warnings.filterwarnings("ignore")
from ultralytics import YOLO
out = {}
for label, path in (("plate_detector.pt", "D:/ANPR/models/plate_detector.pt"),
                    ("plate_detector_s.pt", "D:/ANPR/models/plate_detector_s.pt")):
    for imgsz in (640, 960):
        try:
            r = YOLO(path).val(data="dataset/v2/data_test.yaml", imgsz=imgsz, conf=0.001,
                               iou=0.5, device="cpu", split="val", project="runs/baseline",
                               name=f"{label}_{imgsz}", exist_ok=True, plots=False, verbose=False)
            out[f"{label}@{imgsz}"] = dict(mAP50=round(float(r.box.map50), 4),
                                           mAP50_95=round(float(r.box.map), 4),
                                           precision=round(float(r.box.mp), 4),
                                           recall=round(float(r.box.mr), 4))
        except Exception as e:
            out[f"{label}@{imgsz}"] = {"error": str(e)}
        json.dump(out, open("reports/baseline_metrics.json", "w"), indent=2)
print(json.dumps(out, indent=2))
