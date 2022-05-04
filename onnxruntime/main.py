import cv2
import argparse
import numpy as np
import onnxruntime as ort

class yolov5():
    def __init__(self, modelpath, confThreshold=0.5, nmsThreshold=0.5, objThreshold=0.5):
        with open('class.names', 'rt') as f:
            self.classes = f.read().rstrip('\n').split('\n')
        self.num_classes = len(self.classes)
        anchors = [[27, 26, 20, 40, 44, 19, 34, 34, 25, 47], [55, 24, 44, 38, 31, 61, 50, 50, 63, 45], [65, 62, 88, 60, 84, 79, 113, 85, 148, 122]]
        self.nl = len(anchors)
        self.na = len(anchors[0]) // 2
        self.grid = [np.zeros(1)] * self.nl
        self.stride = np.array([8., 16., 32.])
        self.anchor_grid = np.asarray(anchors, dtype=np.float32).reshape(self.nl, -1, 2)
        self.inpWidth = 1024
        self.inpHeight = 1024
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.net = ort.InferenceSession(modelpath, so)
        self.confThreshold = confThreshold
        self.nmsThreshold = nmsThreshold
        self.objThreshold = objThreshold

    def resize_image(self, srcimg, keep_ratio=True):
        top, left, newh, neww = 0, 0, self.inpWidth, self.inpHeight
        if keep_ratio and srcimg.shape[0] != srcimg.shape[1]:
            hw_scale = srcimg.shape[0] / srcimg.shape[1]
            if hw_scale > 1:
                newh, neww = self.inpHeight, int(self.inpWidth / hw_scale)
                img = cv2.resize(srcimg, (neww, newh), interpolation=cv2.INTER_AREA)
                left = int((self.inpWidth - neww) * 0.5)
                img = cv2.copyMakeBorder(img, 0, 0, left, self.inpWidth - neww - left, cv2.BORDER_CONSTANT,
                                         value=(114, 114, 114))  # add border
            else:
                newh, neww = int(self.inpHeight * hw_scale), self.inpWidth
                img = cv2.resize(srcimg, (neww, newh), interpolation=cv2.INTER_AREA)
                top = int((self.inpHeight - newh) * 0.5)
                img = cv2.copyMakeBorder(img, top, self.inpHeight - newh - top, 0, 0, cv2.BORDER_CONSTANT,
                                         value=(114, 114, 114))
        else:
            img = cv2.resize(srcimg, (self.inpWidth, self.inpHeight), interpolation=cv2.INTER_AREA)
        return img, newh, neww, top, left

    def _make_grid(self, nx=20, ny=20):
        xv, yv = np.meshgrid(np.arange(ny), np.arange(nx))
        return np.stack((xv, yv), 2).reshape((-1, 2)).astype(np.float32)

    def preprocess(self, img):
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img = img.astype(np.float32) / 255.0
        return img

    def postprocess(self, frame, outs, padsize=None):
        frameHeight = frame.shape[0]
        frameWidth = frame.shape[1]
        newh, neww, padh, padw = padsize
        ratioh, ratiow = frameHeight / newh, frameWidth / neww
        # Scan through all the bounding boxes output from the network and keep only the
        # ones with high confidence scores. Assign the box's class label as the class with the highest score.

        confidences = []
        boxes = []
        classIds = []
        for detection in outs:
            if detection[6] > self.objThreshold:
                scores = detection[7:]
                classId = np.argmax(scores)
                confidence = scores[classId] * detection[6]
                if confidence > self.confThreshold:
                    # theta_pred = np.arctan(detection[5] / detection[4])*180/np.pi
                    theta_pred = np.arccos(detection[4]) * 180 / np.pi

                    center_x = int((detection[0] - padw) * ratiow)
                    center_y = int((detection[1] - padh) * ratioh)
                    width = int(detection[2] * ratiow)
                    height = int(detection[3] * ratioh)

                    confidences.append(float(confidence))
                    boxes.append([[center_x, center_y], [width, height], float(theta_pred)])
                    classIds.append(classId)
        # Perform non maximum suppression to eliminate redundant overlapping boxes with
        # lower confidences.
        indices = cv2.dnn.NMSBoxesRotated(boxes, confidences, self.confThreshold, self.nmsThreshold).flatten()
        for i in indices:
            vertices = cv2.boxPoints(boxes[i]).astype(np.int32)
            frame = self.drawPred(frame, classIds[i], confidences[i], vertices)
        return frame

    def drawPred(self, frame, classId, conf, vertices):
        # Draw a bounding box.
        x, y = np.min(vertices[:, 0]), np.min(vertices[:, 1]) - 20
        cv2.polylines(frame, [vertices.reshape((-1, 1, 2))], True, (0, 0, 255), thickness=2)

        label = '%.2f' % conf
        label = '%s:%s' % (self.classes[classId], label)
        # Display the label at the top of the bounding box
        cv2.putText(frame, label, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), thickness=1)
        return frame

    def detect(self, srcimg):
        img, newh, neww, padh, padw = self.resize_image(srcimg)
        img = self.preprocess(img)
        blob = np.expand_dims(np.transpose(img, (2, 0, 1)), axis=0)
        outs = self.net.run(None, {self.net.get_inputs()[0].name: blob})[0].squeeze(axis=0)

        # inference output
        outs[:, :5] = 1 / (1 + np.exp(-outs[:, :5]))
        outs[:, 5] = np.tanh(outs[:, 5])
        outs[:, 6:] = 1 / (1 + np.exp(-outs[:, 6:]))
        row_ind = 0
        for i in range(self.nl):
            h, w = int(self.inpHeight / self.stride[i]), int(self.inpWidth / self.stride[i])
            length = int(self.na * h * w)
            if self.grid[i].shape[2:4] != (h, w):
                self.grid[i] = self._make_grid(w, h)

            outs[row_ind:row_ind + length, 0:2] = (outs[row_ind:row_ind + length, 0:2] * 2. - 0.5 + np.tile(
                self.grid[i], (self.na, 1))) * int(self.stride[i])
            outs[row_ind:row_ind + length, 2:4] = (outs[row_ind:row_ind + length, 2:4] * 2) ** 2 * np.repeat(
                self.anchor_grid[i], h * w, axis=0)
            row_ind += length
        srcimg = self.postprocess(srcimg, outs, padsize=(newh, neww, padh, padw))
        return srcimg

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--imgpath", type=str, default='images/1070.png', help="image path")
    parser.add_argument('--modelpath', type=str, default='best.onnx')
    parser.add_argument('--confThreshold', default=0.5, type=float, help='class confidence')
    parser.add_argument('--nmsThreshold', default=0.5, type=float, help='nms iou thresh')
    parser.add_argument('--objThreshold', default=0.5, type=float, help='object confidence')
    args = parser.parse_args()

    yolonet = yolov5(args.modelpath, confThreshold=args.confThreshold, nmsThreshold=args.nmsThreshold,
                     objThreshold=args.objThreshold)
    srcimg = cv2.imread(args.imgpath)
    srcimg = yolonet.detect(srcimg)

    winName = 'Deep learning object detection in ONNXRuntime'
    cv2.namedWindow(winName, 0)
    cv2.imshow(winName, srcimg)
    cv2.waitKey(0)
    cv2.destroyAllWindows()