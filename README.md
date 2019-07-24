# Obama-Lip-Sync
An implementation of ObamaNet: Photo-realistic lip-sync from text (Kumar, Rithesh, et al. "ObamaNet: Photo-realistic lip-sync from text." arXiv preprint arXiv:1801.01442. 2017.). Implementation does not include an audio to text engine but trains directly on audio. All relevant data can be found in the data folder.
![alt text](results/5.png)

To generate mouth shape given audio run

Download [Link](https://drive.google.com/drive/folders/1IE87yn1p5Rh8rKDfBb_NkAOuIxE2Jghr?usp=sharing) to normalized data and PCA files used to train on, a sample target video and its inverse normalization points, trained model with look back of 500ms, 300ms, 100ms.

```
python run.py --sf sampleAudio.wav --mf path/obama.h5 --lb 10
````

Download checkpoints [Link](https://drive.google.com/file/d/1xVD2qjyJodTbO3c_-14zwPF5ctwLAFYp/view) to a trained pix2pix model.

To use pix2pix run 
```
python pix2pix.py --mode test --output_dir test_output/ --input_dir output/ --checkpoint Pix2PixModel/
```

To generate final video run
```
ffmpeg -r 32 -f image2 -s 256x256 -i test_output/images/%d-outputs.png -vcodec libx264 -crf 25 outputa.mp4
ffmpeg -i outputa.mp4 -i sampleAudio.wav -c:v copy -c:a aac -strict experimental output.mp4
```
