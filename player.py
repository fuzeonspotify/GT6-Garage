from garagehub.desktop import desktop_parser, dialog, open_workshop

if __name__ == '__main__':
    from garagehub.template_build import dispatch
    dispatch()
    parser = desktop_parser()
    parser.add_argument('--workshop',dest='model_tester',action='store_false',help='Open the car workshop directly.')
    parser.set_defaults(model_tester=True)
    options = parser.parse_args()
    if options.dialog:
        dialog(options.dialog, options.dialog_result)
    else:
        raise SystemExit(open_workshop(options))
