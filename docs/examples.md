# Examples

Scripts in [`examples/`](https://github.com/Anonoei/pysdrlib/tree/main/examples). Each runs on the simulated radio
(no hardware), and `tests/test_examples.py` runs every one, so the code in the guides is the code that is tested.

| Script | Shows | Guide |
| --- | --- | --- |
| `quickstart.py` | Find, open, tune and read | [Receiving](guide/receiving.md) |
| `receiving.py` | `read` with `RxMeta`, `read_into`, retunes, lost samples, stalls, `stream`, `sweep` | [Receiving](guide/receiving.md) |
| `controls.py` | `caps`, gain as a fraction, in dB and by stage, settings, `strict` | [Controls](guide/controls.md) |
| `channels.py` | Two channels received and transmitted together | [Controls](guide/controls.md#channels) |
| `files.py` | Raw files, SigMF with stream metadata, a recording as a radio, transmitting into a file | [Sample files](guide/files.md) |
| `remote.py` | A server, a controller and a viewer, the server's lists and counters (needs `pysdrlib[remote]`) | [Remote radios](guide/remote.md) |
| `custom_driver.py` | A `Device` written and registered outside pysdrlib | [Drivers of your own](guide/custom-drivers.md) |

```
python examples/quickstart.py
```
