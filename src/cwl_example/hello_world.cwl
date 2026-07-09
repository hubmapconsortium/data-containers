cwlVersion: v1.2

class: CommandLineTool
baseCommand: echo
stdout: out.txt
inputs:
  message:
    type: string
    default: "Hello World"
    inputBinding:
      position: 1
outputs:
  hello_out:
    type: stdout


