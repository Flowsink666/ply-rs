# Upstream reference repositories

These directories contain independent Git clones used as reference material.
They are ignored by the main repository; their Git history stays intact.

| Directory | Source | Reference commit |
| --- | --- | --- |
| `ply` | https://github.com/dabeaz/ply.git | `9d7c40099e23ff78f9d86ef69a26c1e8a83e706a` |
| `sly` | https://github.com/dabeaz/sly.git | `09c2ba30df9143298ae5eef79d0baf23755d4cd9` |

To recreate these reference clones, run from the project root:

```sh
git clone https://github.com/dabeaz/ply.git upstream/ply
git -C upstream/ply checkout 9d7c40099e23ff78f9d86ef69a26c1e8a83e706a
git clone https://github.com/dabeaz/sly.git upstream/sly
git -C upstream/sly checkout 09c2ba30df9143298ae5eef79d0baf23755d4cd9
```
