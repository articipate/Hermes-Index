codes:
/A = src/
/A/1 = src/main.py
/A/A = src/utils/
/A/B = src/components/
/A/B/1 = src/components/button.py
/1 = README.md

map:
1: /A(Create source folder)
2: /A/1(Create file)
3: /A/1(Write the parser)
4: /A/A(Create folder)
5: /A/B(Create folder)
6: /A/B/1(Create button component)
7: /1(Create file)
8: /1(Document project usage)

<!-- h-index-state
{
  "version": 1,
  "created_at": "2026-10-26T19:42:50Z",
  "updated_at": "2026-10-26T19:43:06Z",
  "next_order": 9,
  "folders": {
    "/A": {
      "path": "src",
      "parent": "/",
      "id_parent": "/",
      "segment": "A",
      "created_order": 1,
      "status": "active"
    },
    "/A/A": {
      "path": "src/utils",
      "parent": "/A",
      "id_parent": "/A",
      "segment": "A",
      "created_order": 4,
      "status": "active"
    },
    "/A/B": {
      "path": "src/components",
      "parent": "/A",
      "id_parent": "/A",
      "segment": "B",
      "created_order": 5,
      "status": "active"
    }
  },
  "files": {
    "/A/1": {
      "path": "src/main.py",
      "folder": "/A",
      "id_folder": "/A",
      "number": 1,
      "created_order": 2,
      "last_order": 3,
      "status": "active"
    },
    "/A/B/1": {
      "path": "src/components/button.py",
      "folder": "/A/B",
      "id_folder": "/A/B",
      "number": 1,
      "created_order": 6,
      "last_order": 6,
      "status": "active"
    },
    "/1": {
      "path": "README.md",
      "folder": "/",
      "id_folder": "/",
      "number": 1,
      "created_order": 7,
      "last_order": 8,
      "status": "active"
    }
  },
  "counters": {
    "/": {
      "next_file": 2,
      "used_segments": [
        "A"
      ]
    },
    "/A": {
      "next_file": 2,
      "used_segments": [
        "A",
        "B"
      ]
    },
    "/A/A": {
      "next_file": 1,
      "used_segments": []
    },
    "/A/B": {
      "next_file": 2,
      "used_segments": []
    }
  },
  "log": [
    {
      "order": 1,
      "id": "/A",
      "action": "Create source folder",
      "kind": "folder",
      "op": "create",
      "path": "src",
      "timestamp": "2026-10-26T19:42:52Z"
    },
    {
      "order": 2,
      "id": "/A/1",
      "action": "Create file",
      "kind": "file",
      "op": "create",
      "path": "src/main.py",
      "timestamp": "2026-10-26T19:42:54Z"
    },
    {
      "order": 3,
      "id": "/A/1",
      "action": "Write the parser",
      "kind": "file",
      "op": "modify",
      "path": "src/main.py",
      "timestamp": "2026-10-26T19:42:56Z"
    },
    {
      "order": 4,
      "id": "/A/A",
      "action": "Create folder",
      "kind": "folder",
      "op": "create",
      "path": "src/utils",
      "timestamp": "2026-10-26T19:42:58Z"
    },
    {
      "order": 5,
      "id": "/A/B",
      "action": "Create folder",
      "kind": "folder",
      "op": "create",
      "path": "src/components",
      "timestamp": "2026-10-26T19:43:00Z"
    },
    {
      "order": 6,
      "id": "/A/B/1",
      "action": "Create button component",
      "kind": "file",
      "op": "create",
      "path": "src/components/button.py",
      "timestamp": "2026-10-26T19:43:02Z"
    },
    {
      "order": 7,
      "id": "/1",
      "action": "Create file",
      "kind": "file",
      "op": "create",
      "path": "README.md",
      "timestamp": "2026-10-26T19:43:04Z"
    },
    {
      "order": 8,
      "id": "/1",
      "action": "Document project usage",
      "kind": "file",
      "op": "modify",
      "path": "README.md",
      "timestamp": "2026-10-26T19:43:06Z"
    }
  ]
}
-->
