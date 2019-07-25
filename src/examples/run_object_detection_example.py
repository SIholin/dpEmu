import json
import sys
from abc import ABC, abstractmethod

import cv2
import detectron.utils.c2 as c2_utils
import matplotlib.pyplot as plt
import numpy as np
import torch
from caffe2.python import workspace
from detectron.core.config import assert_and_infer_cfg, reset_cfg
from detectron.core.config import cfg
from detectron.core.config import merge_cfg_from_file
from detectron.core.config import merge_cfg_from_list
from detectron.core.test_engine import run_inference
from detectron.utils.logging import setup_logging
from numpy.random import RandomState
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
from tqdm import trange

from src import runner_
from src.datasets.utils import load_coco_val_2017
from src.plotting.utils import print_results, visualize_scores
from src.problemgenerator.array import Array
from src.problemgenerator.filters import JPEG_Compression
from src.problemgenerator.series import Series
from src.utils import generate_unique_path

c2_utils.import_detectron_ops()
cv2.ocl.setUseOpenCL(False)
torch.multiprocessing.set_start_method("spawn", force="True")


class Preprocessor:
    def run(self, _, imgs):
        return None, imgs, {}


class AbstractDetectronModel(ABC):

    def __init__(self):
        self.random_state = RandomState(42)

        workspace.GlobalInit(["caffe2", "--caffe2_log_level=0"])
        setup_logging(__name__)

    def run(self, _, imgs, params):
        img_ids = params["img_ids"]
        path_to_cfg = self.get_path_to_cfg()
        url_to_weights = self.get_url_to_weights()

        reset_cfg()
        merge_cfg_from_file(path_to_cfg)
        opt_list = [
            "MODEL.MASK_ON",
            False,
            "NUM_GPUS",
            "1",
            "TEST.DATASETS",
            ("coco_2017_val",),
            # "TEST.SCALE",
            # "300",
            "TEST.WEIGHTS",
            url_to_weights,
            "OUTPUT_DIR",
            "tmp"
        ]
        merge_cfg_from_list(opt_list)
        assert_and_infer_cfg(make_immutable=False)

        results = run_inference(imgs, img_ids, cfg.TEST.WEIGHTS)
        return {"mAP-50": round(results["coco_2017_val"]["box"]["AP50"], 3)}

    @abstractmethod
    def get_path_to_cfg(self):
        pass

    @abstractmethod
    def get_url_to_weights(self):
        pass


class FasterRCNNModel(AbstractDetectronModel):
    def __init__(self):
        super().__init__()

    def get_path_to_cfg(self):
        return "venv/src/detectron/configs/12_2017_baselines/e2e_faster_rcnn_X-101-64x4d-FPN_1x.yaml"

    def get_url_to_weights(self):
        return (
            "https://dl.fbaipublicfiles.com/detectron/35858015/12_2017_baselines/"
            "e2e_faster_rcnn_X-101-64x4d-FPN_1x.yaml.01_40_54.1xc565DE/output/train/"
            "coco_2014_train%3Acoco_2014_valminusminival/generalized_rcnn/model_final.pkl"
        )


class MaskRCNNModel(AbstractDetectronModel):
    def __init__(self):
        super().__init__()

    def get_path_to_cfg(self):
        return "venv/src/detectron/configs/12_2017_baselines/e2e_mask_rcnn_X-101-64x4d-FPN_1x.yaml"

    def get_url_to_weights(self):
        return (
            "https://dl.fbaipublicfiles.com/detectron/36494496/12_2017_baselines/"
            "e2e_mask_rcnn_X-101-64x4d-FPN_1x.yaml.07_50_11.fkwVtEvg/output/train/"
            "coco_2014_train%3Acoco_2014_valminusminival/generalized_rcnn/model_final.pkl"
        )


class RetinaNetModel(AbstractDetectronModel):
    def __init__(self):
        super().__init__()

    def get_path_to_cfg(self):
        return "venv/src/detectron/configs/12_2017_baselines/retinanet_X-101-64x4d-FPN_1x.yaml"

    def get_url_to_weights(self):
        return (
            "https://dl.fbaipublicfiles.com/detectron/36768875/12_2017_baselines/"
            "retinanet_X-101-64x4d-FPN_1x.yaml.08_34_37.FSXgMpzP/output/train/"
            "coco_2014_train%3Acoco_2014_valminusminival/retinanet/model_final.pkl"
        )


class YOLOv3Model:

    def __init__(self):
        self.random_state = RandomState(42)
        self.coco91class = [
            1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28, 31, 32, 33,
            34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61,
            62, 63, 64, 65, 67, 70, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 84, 85, 86, 87, 88, 89, 90
        ]
        self.show_imgs = False

    @staticmethod
    def __draw_box(img, class_id, class_names, confidence, x, y, w, h):
        label = str(class_names[class_id]) + " " + str(confidence)
        colors = np.random.randint(0, 255, size=(len(class_names), 3), dtype="uint8")
        color = [int(c) for c in colors[class_id]]
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(img, label, (x, y - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    def __get_results_for_img(self, img, img_id, class_names, net):
        conf_threshold = 0
        img_h = img.shape[0]
        img_w = img.shape[1]
        inference_size = 608
        scale = 1 / 255

        blob = cv2.dnn.blobFromImage(img, scale, (inference_size, inference_size), (0, 0, 0), True)
        net.setInput(blob)
        out_layer_names = net.getUnconnectedOutLayersNames()
        outs = net.forward(out_layer_names)

        results = []
        for out in outs:
            for detection in out:
                scores = detection[5:]
                class_id = np.argmax(scores)
                conf = float(scores[class_id])
                if conf > conf_threshold:
                    center_x = detection[0] * img_w
                    center_y = detection[1] * img_h
                    w = detection[2] * img_w
                    h = detection[3] * img_h
                    x = center_x - w / 2
                    y = center_y - h / 2
                    results.append({
                        "image_id": img_id,
                        "category_id": self.coco91class[class_id],
                        "bbox": [x, y, w, h],
                        "score": conf,
                    })

                    if self.show_imgs:
                        self.__draw_box(img, class_id, class_names, round(conf, 2), int(round(x)), int(round(y)),
                                        int(round(w)), int(round(h)))

        if self.show_imgs:
            cv2.imshow(str(img_id), img)
            cv2.waitKey()
            cv2.destroyAllWindows()

        return results

    def run(self, _, imgs, model_params):
        img_ids = model_params["img_ids"]
        class_names = model_params["class_names"]
        self.show_imgs = model_params["show_imgs"]

        net = cv2.dnn.readNet("tmp/yolov3-spp_best.weights", "tmp/yolov3-spp.cfg")
        results = []
        for i in trange(len(imgs)):
            results.extend(self.__get_results_for_img(imgs[i], img_ids[i], class_names, net))
        if not results:
            return {"mAP-50": 0}

        path_to_results = generate_unique_path("tmp", "json")
        with open(path_to_results, "w") as fp:
            json.dump(results, fp)

        coco_gt = COCO("data/annotations/instances_val2017.json")
        coco_eval = COCOeval(coco_gt, coco_gt.loadRes(path_to_results), "bbox")
        coco_eval.params.imgIds = img_ids
        coco_eval.evaluate()
        coco_eval.accumulate()
        coco_eval.summarize()
        return {"mAP-50": round(coco_eval.stats[1], 3)}


def visualize(df):
    # visualize_scores(df, ["mAP-50"], [True], "std", "Object detection with Gaussian noise", log=False)
    # visualize_scores(df, ["mAP-50"], [True], "std", "Object detection with Gaussian blur", log=False)
    # visualize_scores(df, ["mAP-50"], [True], "snowflake_probability", "Object detection with snow filter", log=True)
    # visualize_scores(df, ["mAP-50"], [True], "probability", "Object detection with rain filter", log=True)
    # visualize_scores(df, ["mAP-50"], [True], "probability", "Object detection with added stains", log=True)
    visualize_scores(df, ["mAP-50"], [True], "quality", "Object detection with JPEG compression", log=False)

    plt.show()


def main(argv):
    if len(argv) != 2:
        exit(0)

    imgs, img_ids, class_names = load_coco_val_2017(int(argv[1]))

    err_node = Array()
    err_root_node = Series(err_node)

    # err_node.addfilter(GaussianNoise("mean", "std"))
    # err_node.addfilter(Blur_Gaussian("std"))
    # err_node.addfilter(Snow("snowflake_probability", "snowflake_alpha", "snowstorm_alpha"))
    # err_node.addfilter(Rain("probability"))
    # err_node.addfilter(StainArea("probability", "radius_generator", "transparency_percentage"))
    err_node.addfilter(JPEG_Compression("quality"))

    # err_params_list = [{"mean": 0, "std": std} for std in [10 * i for i in range(0, 4)]]
    # err_params_list = [{"std": std} for std in [i for i in range(0, 4)]]
    # err_params_list = [{"snowflake_probability": p, "snowflake_alpha": .4, "snowstorm_alpha": 0}
    #                    for p in [10 ** i for i in range(-4, 0)]]
    # err_params_list = [{"probability": p} for p in [10 ** i for i in range(-4, 0)]]
    # err_params_list = [
    #     {"probability": p, "radius_generator": GaussianRadiusGenerator(0, 50), "transparency_percentage": 0.2}
    #     for p in [10 ** i for i in range(-6, -2)]]
    err_params_list = [{"quality": q} for q in [10, 20, 30, 100]]

    model_params_dict_list = [
        {"model": YOLOv3Model, "params_list": [{"img_ids": img_ids, "class_names": class_names, "show_imgs": False}]},
        # {"model": FasterRCNNModel, "params_list": [{"img_ids": img_ids}]},
        # {"model": MaskRCNNModel, "params_list": [{"img_ids": img_ids}]},
        # {"model": RetinaNetModel, "params_list": [{"img_ids": img_ids}]},
    ]

    df = runner_.run(None, imgs, Preprocessor, err_root_node, err_params_list, model_params_dict_list, n_processes=1)

    print_results(df, ["img_ids", "class_names", "show_imgs", "mean", "std", "radius_generator",
                       "transparency_percentage"])
    visualize(df)


if __name__ == "__main__":
    main(sys.argv)